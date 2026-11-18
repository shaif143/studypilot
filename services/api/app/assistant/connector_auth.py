"""Signing in to a connector.

A remote MCP server that needs an account says so in a standard way, and
this module follows it (the MCP authorization specification, which is OAuth
2.1 with a few discovery steps in front):

1. **Where to sign in.** The server's protected-resource metadata names its
   authorisation server; that server's own metadata names its endpoints.
2. **Who is asking.** This application registers itself with the
   authorisation server on the spot (dynamic client registration), or, where
   the server prefers it and this site has a public address, identifies itself
   by a document it serves (a client-id metadata document).
3. **The sign-in.** The browser is sent to the authorisation page with a PKCE
   challenge. It comes back with a code, which is exchanged for an access
   token, and usually a refresh token.

The token is what Anthropic's API presents to the connector. It, the refresh
token and the registration are kept encrypted beside the connector.

Every address here was typed by a user or named by a server a user chose, and
this process fetches them. So each request is made only to an ``https``
address whose host resolves to public addresses, is sent to the address that
was checked (a second lookup cannot swap in a private one), follows a
redirect only after checking it the same way, and is limited in time and
size.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import re
import secrets
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

log = logging.getLogger(__name__)

CALLBACK_PATH = "/api/assistant/connectors/oauth/callback"
CLIENT_DOCUMENT_PATH = "/api/assistant/connectors/oauth/client.json"
CLIENT_NAME = "StudyPilot"
#: How long a sign-in may take between leaving for the consent page and returning.
FLOW_SECONDS = 15 * 60
#: A token this close to expiring is renewed before it is used.
RENEW_BEFORE_SECONDS = 120
TIMEOUT = 10.0
MAX_RESPONSE_BYTES = 256 * 1024
MAX_REDIRECTS = 3

#: Tests replace this to answer requests without a network.
TRANSPORT: httpx.BaseTransport | None = None


class ConnectorAuthError(Exception):
    """A sign-in could not go ahead; the message is for the user."""


# --- fetching addresses that came from outside ---------------------------------
def _public_address(host: str, port: int) -> str:
    """One address the host resolves to, having checked that all of them are public."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ConnectorAuthError(f"The address {host} could not be found.") from exc
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise ConnectorAuthError(f"The address {host} could not be found.")
    for address in addresses:
        if not ipaddress.ip_address(address.split("%", 1)[0]).is_global:
            raise ConnectorAuthError(
                f"{host} is not on the public internet, so it cannot be used here."
            )
    # IPv4 first: it is what every host this runs on can reach.
    return next((address for address in addresses if ":" not in address), addresses[0])


def _checked(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or not host:
        raise ConnectorAuthError("Sign-in addresses must start with https://.")
    if parts.username or parts.password:
        raise ConnectorAuthError("A sign-in address may not carry a password.")
    return url, host, parts.port or 443


def fetch(
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """One guarded request. Redirects are followed for GET only, each one checked."""
    sent = {"accept": "application/json", **(headers or {})}
    for _hop in range(MAX_REDIRECTS + 1):
        url, host, port = _checked(url)
        try:
            if TRANSPORT is not None:
                with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT) as client:
                    response = client.request(method, url, json=json_body, data=form, headers=sent)
            else:
                address = _public_address(host, port)
                parts = urlsplit(url)
                literal = f"[{address}]" if ":" in address else address
                target = parts._replace(netloc=f"{literal}:{port}").geturl()
                # Sent to the address that was checked, as the host it claims
                # to be: the certificate is still verified against the name.
                with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
                    response = client.request(
                        method,
                        target,
                        json=json_body,
                        data=form,
                        headers={**sent, "host": parts.netloc},
                        extensions={"sni_hostname": host},
                    )
        except httpx.HTTPError as exc:
            raise ConnectorAuthError(f"{host} could not be reached.") from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ConnectorAuthError(f"{host} sent more than a sign-in reply should hold.")
        if response.status_code in {301, 302, 303, 307, 308} and method == "GET":
            location = response.headers.get("location", "")
            if not location:
                break
            url = str(httpx.URL(url).join(location))
            continue
        return response
    raise ConnectorAuthError("The sign-in address redirected too many times.")


def _json(response: httpx.Response) -> dict[str, Any] | None:
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# --- discovery -----------------------------------------------------------------
@dataclass
class Server:
    """What a connector's authorisation server offers."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str = ""
    #: It accepts a client identified by a document at an https address.
    accepts_client_document: bool = False
    scope: str = ""
    #: The connector's own address as it names itself, sent as ``resource``.
    resource: str = ""


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _challenge(resource_url: str) -> tuple[str, str]:
    """Ask the connector unauthenticated; it answers with where its metadata is."""
    try:
        response = fetch(
            "POST",
            resource_url,
            json_body={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"accept": "application/json, text/event-stream"},
        )
    except ConnectorAuthError:
        return "", ""
    header = response.headers.get("www-authenticate", "")
    metadata = re.search(r'resource_metadata="([^"]+)"', header)
    scope = re.search(r'scope="([^"]*)"', header)
    return (metadata.group(1) if metadata else ""), (scope.group(1) if scope else "")


def _resource_metadata(resource_url: str, hinted: str) -> dict[str, Any] | None:
    parts = urlsplit(resource_url)
    origin = _origin(resource_url)
    path = parts.path.rstrip("/")
    candidates = [hinted] if hinted else []
    if path:
        candidates.append(f"{origin}/.well-known/oauth-protected-resource{path}")
    candidates.append(f"{origin}/.well-known/oauth-protected-resource")
    for candidate in dict.fromkeys(candidates):
        try:
            data = _json(fetch("GET", candidate))
        except ConnectorAuthError:
            continue
        if data and isinstance(data.get("authorization_servers"), list):
            return data
    return None


def _server_metadata(issuer: str) -> dict[str, Any] | None:
    parts = urlsplit(issuer)
    origin = _origin(issuer)
    path = parts.path.rstrip("/")
    if path:
        candidates = [
            f"{origin}/.well-known/oauth-authorization-server{path}",
            f"{origin}/.well-known/openid-configuration{path}",
            f"{origin}{path}/.well-known/openid-configuration",
        ]
    else:
        candidates = [
            f"{origin}/.well-known/oauth-authorization-server",
            f"{origin}/.well-known/openid-configuration",
        ]
    for candidate in candidates:
        try:
            data = _json(fetch("GET", candidate))
        except ConnectorAuthError:
            continue
        if data and data.get("authorization_endpoint") and data.get("token_endpoint"):
            return data
    return None


def discover(resource_url: str) -> Server:
    """Find where to sign in for a connector, or say that it offers no sign-in."""
    hinted, challenged_scope = _challenge(resource_url)
    resource = _resource_metadata(resource_url, hinted)
    issuers: list[str] = []
    scope = challenged_scope
    named_resource = resource_url
    if resource:
        issuers = [str(item) for item in resource["authorization_servers"] if item]
        named_resource = str(resource.get("resource") or resource_url)
        if not scope and isinstance(resource.get("scopes_supported"), list):
            scope = " ".join(str(item) for item in resource["scopes_supported"])
    # Servers written to the first version of the specification are their own
    # authorisation server and publish no resource metadata.
    issuers.append(_origin(resource_url))
    for issuer in dict.fromkeys(issuers):
        metadata = _server_metadata(issuer)
        if metadata is None:
            continue
        methods = metadata.get("code_challenge_methods_supported")
        if isinstance(methods, list) and "S256" not in methods:
            continue
        return Server(
            issuer=str(metadata.get("issuer") or issuer),
            authorization_endpoint=str(metadata["authorization_endpoint"]),
            token_endpoint=str(metadata["token_endpoint"]),
            registration_endpoint=str(metadata.get("registration_endpoint") or ""),
            accepts_client_document=bool(metadata.get("client_id_metadata_document_supported")),
            scope=scope,
            resource=named_resource,
        )
    raise ConnectorAuthError(
        "This connector does not offer a sign-in. If it needs an access token, paste one "
        "under Edit; if it needs none, it can be used as it is."
    )


# --- registration ----------------------------------------------------------------
def client_document(document_url: str, redirect_uri: str) -> dict[str, Any]:
    """This application, described for a server that identifies clients by document."""
    return {
        "client_id": document_url,
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


def register(server: Server, redirect_uri: str, document_url: str) -> dict[str, str]:
    """Make this application known to the authorisation server.

    Returns ``{"client_id", "client_secret", "auth_method"}``. Registering on
    the spot is tried first, being what most servers have offered longest; a
    server with no registration endpoint that accepts a client document is
    given this site's, when the site has a public address to serve it from.
    """
    if not server.registration_endpoint:
        if server.accepts_client_document and document_url.startswith("https://"):
            return {"client_id": document_url, "client_secret": "", "auth_method": "none"}
        raise ConnectorAuthError(
            "This connector's sign-in needs an app to be registered with it by hand, which "
            "cannot be done from here. If it offers access tokens, paste one under Edit."
        )
    response = fetch(
        "POST",
        server.registration_endpoint,
        json_body={
            "client_name": CLIENT_NAME,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    try:
        data = response.json() if response.status_code in {200, 201} else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or not data.get("client_id"):
        raise ConnectorAuthError("The connector would not register this app for sign-in.")
    secret = str(data.get("client_secret") or "")
    method = str(data.get("token_endpoint_auth_method") or "")
    if not method:
        method = "client_secret_basic" if secret else "none"
    return {"client_id": str(data["client_id"]), "client_secret": secret, "auth_method": method}


# --- the sign-in itself -----------------------------------------------------------
def new_verifier() -> str:
    return secrets.token_urlsafe(64)[:96]


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(
    server: Server, client: dict[str, str], *, redirect_uri: str, state: str, verifier: str
) -> str:
    query = {
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge_for(verifier),
        "code_challenge_method": "S256",
        "resource": server.resource,
    }
    if server.scope:
        query["scope"] = server.scope
    _checked(server.authorization_endpoint)
    joiner = "&" if "?" in server.authorization_endpoint else "?"
    return f"{server.authorization_endpoint}{joiner}{urlencode(query)}"


def _token_request(pending: dict[str, Any], form: dict[str, str]) -> dict[str, Any]:
    headers: dict[str, str] = {}
    form = {**form, "client_id": pending["client_id"]}
    if pending.get("resource"):
        form["resource"] = pending["resource"]
    secret = pending.get("client_secret") or ""
    if secret and pending.get("auth_method") == "client_secret_post":
        form["client_secret"] = secret
    elif secret:
        basic = base64.b64encode(f"{pending['client_id']}:{secret}".encode()).decode("ascii")
        headers["authorization"] = f"Basic {basic}"
    response = fetch("POST", pending["token_endpoint"], form=form, headers=headers)
    try:
        data = response.json()
    except ValueError:
        data = None
    if response.status_code != 200 or not isinstance(data, dict) or not data.get("access_token"):
        reason = ""
        if isinstance(data, dict):
            reason = str(data.get("error_description") or data.get("error") or "")
        raise ConnectorAuthError(
            "The connector did not give out a token" + (f": {reason}." if reason else ".")
        )
    return data


def _with_tokens(pending: dict[str, Any], tokens: dict[str, Any]) -> dict[str, Any]:
    """The record kept for a signed-in connector."""
    expires_in = tokens.get("expires_in")
    try:
        expires_at = time.time() + float(expires_in) if expires_in else 0.0
    except (TypeError, ValueError):
        expires_at = 0.0
    return {
        "client_id": pending["client_id"],
        "client_secret": pending.get("client_secret", ""),
        "auth_method": pending.get("auth_method", "none"),
        "token_endpoint": pending["token_endpoint"],
        "resource": pending.get("resource", ""),
        "access_token": str(tokens["access_token"]),
        # A refresh that returns no new refresh token keeps the old one.
        "refresh_token": str(tokens.get("refresh_token") or pending.get("refresh_token") or ""),
        "expires_at": expires_at,
    }


def exchange(pending: dict[str, Any], *, code: str) -> dict[str, Any]:
    """Trade the code the browser brought back for tokens."""
    tokens = _token_request(
        pending,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending["redirect_uri"],
            "code_verifier": pending["verifier"],
        },
    )
    return _with_tokens(pending, tokens)


def needs_renewal(record: dict[str, Any], now: float | None = None) -> bool:
    expires_at = float(record.get("expires_at") or 0)
    if not expires_at or not record.get("refresh_token"):
        return False
    return (now if now is not None else time.time()) > expires_at - RENEW_BEFORE_SECONDS


def renew(record: dict[str, Any]) -> dict[str, Any]:
    """A fresh access token from the refresh token."""
    tokens = _token_request(
        record, {"grant_type": "refresh_token", "refresh_token": record["refresh_token"]}
    )
    return _with_tokens(record, tokens)


def dumps(record: dict[str, Any]) -> str:
    return json.dumps(record, separators=(",", ":"))


def loads(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
