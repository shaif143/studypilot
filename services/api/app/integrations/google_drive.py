"""Google Drive, read-only, without copying files onto this machine.

The protocol layer: OAuth 2.0 with PKCE against Google, and the handful of
Drive API calls the app needs — who the account is, what a folder holds, and
the bytes of one file. Nothing here touches the database; `drive_import`
does, using these pieces.

Design points:

* **Read-only scope.** `drive.readonly` is the narrowest scope that can list
  a folder the student chose and read the files in it. The app never writes
  to Drive and asks for nothing else.
* **PKCE and a one-time state.** The authorisation code is bound to a code
  verifier that only this process knows, and the `state` the browser brings
  back must match a flow this process started — so a code pasted into a
  different browser session, or a forged callback, is refused.
* **Files are fetched, indexed and dropped.** `fetch` returns bytes; the
  caller indexes them and keeps none. Google Docs and Slides have no file of
  their own and are exported as PDF through Google's own exporter.
* **Tokens are stored encrypted.** The refresh token is the long-lived
  credential; `TokenCipher` wraps it with a key that lives in the macOS
  Keychain on the desktop and in the operator's environment when hosted.
* **No SDK.** The four endpoints involved are plain HTTPS, and `httpx` is
  already a dependency; Google's client library would add a dozen packages
  for a thin wrapper around the same calls.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets as _secrets
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from app.ingestion.pptx import PPTX_MEDIA_TYPE

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_URL = "https://www.googleapis.com/drive/v3"
#: The one scope the app asks for. Listing and reading; never writing.
SCOPES = ("https://www.googleapis.com/auth/drive.readonly",)

FOLDER_MIME = "application/vnd.google-apps.folder"
#: Google-native formats: no bytes of their own, exported as PDF on read.
EXPORT_AS_PDF = frozenset(
    {
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.presentation",
    }
)
#: Formats the ingestion pipeline reads without a model.
IMPORTABLE_MIMES = frozenset({"application/pdf", PPTX_MEDIA_TYPE}) | EXPORT_AS_PDF
#: A refreshed token is used until this close to expiry, then refreshed.
REFRESH_MARGIN_SECONDS = 120

#: Swapped by tests for an ``httpx.MockTransport``; otherwise the network.
TRANSPORT: httpx.BaseTransport | None = None
#: How the desktop opens the consent page. Tests replace it.
open_in_browser = webbrowser.open


def http_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), transport=TRANSPORT)


class DriveError(Exception):
    """A request to Google failed; the message is safe to show the user."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


class DriveNotConfiguredError(DriveError):
    """No OAuth client has been set up on this installation."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status=409)


class DriveNotConnectedError(DriveError):
    """The user has not linked a Google account, or the link was revoked."""

    def __init__(self, message: str = "Google Drive is not connected.") -> None:
        super().__init__(message, status=409)


# --- the OAuth client -------------------------------------------------------
@dataclass(frozen=True)
class OAuthClient:
    """The Google Cloud OAuth client this installation identifies itself with.

    On the desktop both values come from the Keychain via `secrets` (loaded
    into the environment at startup, like the provider keys); hosted, from
    the operator's environment. The client secret of a *Desktop app* client is
    not treated as confidential by Google, but it is still kept out of files
    and logs here.
    """

    client_id: str = ""
    client_secret: str = ""

    @classmethod
    def from_environment(cls) -> OAuthClient:
        return cls(
            client_id=os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip(),
            client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip(),
        )

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


def new_state() -> str:
    return _secrets.token_urlsafe(24)


def new_verifier() -> str:
    """A PKCE code verifier: 43-128 unreserved characters (RFC 7636)."""
    return _secrets.token_urlsafe(64)


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(client: OAuthClient, redirect_uri: str, state: str, verifier: str) -> str:
    params = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge_for(verifier),
        "code_challenge_method": "S256",
        # A refresh token is only issued with offline access, and only on a
        # consent screen the user actually sees; without `prompt=consent` a
        # re-connect after a disconnect comes back with no refresh token.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "false",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    #: Unix time after which `access_token` is no longer accepted.
    expires_at: float
    scope: str = ""

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - REFRESH_MARGIN_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Tokens:
        return cls(
            access_token=str(data.get("access_token", "")),
            refresh_token=str(data.get("refresh_token", "")),
            expires_at=float(data.get("expires_at", 0)),
            scope=str(data.get("scope", "")),
        )


def _token_request(http: httpx.Client, form: dict[str, str]) -> dict[str, Any]:
    try:
        response = http.post(TOKEN_URL, data=form)
    except httpx.HTTPError as exc:
        raise DriveError(f"Could not reach Google to exchange the sign-in: {exc}") from exc
    if response.status_code != 200:
        detail = _error_detail(response)
        log.warning("google_token_request_failed", extra={"status": response.status_code})
        raise DriveError(f"Google refused the sign-in: {detail}", status=502)
    return response.json()


def exchange_code(
    http: httpx.Client, client: OAuthClient, code: str, redirect_uri: str, verifier: str
) -> Tokens:
    data = _token_request(
        http,
        {
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )
    refresh = str(data.get("refresh_token", ""))
    if not refresh:
        raise DriveError(
            "Google did not return a refresh token. Remove StudyPilot from your Google "
            "account's connected apps and connect again.",
            status=502,
        )
    return Tokens(
        access_token=str(data["access_token"]),
        refresh_token=refresh,
        expires_at=time.time() + float(data.get("expires_in", 3600)),
        scope=str(data.get("scope", "")),
    )


def refresh_tokens(http: httpx.Client, client: OAuthClient, tokens: Tokens) -> Tokens:
    data = _token_request(
        http,
        {
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "refresh_token": tokens.refresh_token,
            "grant_type": "refresh_token",
        },
    )
    return Tokens(
        access_token=str(data["access_token"]),
        # Google keeps the refresh token stable; a new one, if any, replaces it.
        refresh_token=str(data.get("refresh_token") or tokens.refresh_token),
        expires_at=time.time() + float(data.get("expires_in", 3600)),
        scope=str(data.get("scope", tokens.scope)),
    )


def revoke(http: httpx.Client, token: str) -> None:
    """Best effort: a token that is already invalid is the outcome wanted."""
    try:
        http.post(REVOKE_URL, params={"token": token})
    except httpx.HTTPError as exc:
        log.info("google_revoke_failed", extra={"detail": str(exc)})


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or f"HTTP {response.status_code}")
        description = body.get("error_description") or error
        if description:
            return str(description)
    return f"HTTP {response.status_code}"


# --- token encryption ------------------------------------------------------
class TokenCipher:
    """Fernet around the stored tokens.

    The key is 32 random bytes kept where the installation keeps secrets: the
    Keychain on a Mac, the environment when hosted. Losing the key only means
    connecting Drive again — nothing of the student's is encrypted with it.
    """

    def __init__(self, key: bytes) -> None:
        from cryptography.fernet import Fernet

        self._fernet = Fernet(key)

    @staticmethod
    def new_key() -> bytes:
        from cryptography.fernet import Fernet

        return Fernet.generate_key()

    @staticmethod
    def key_from_secret(secret: str) -> bytes:
        """Derive a Fernet key from an operator secret of any length."""
        digest = hashlib.sha256(b"studypilot-drive-tokens:" + secret.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    def encrypt(self, tokens: Tokens) -> str:
        return self._fernet.encrypt(json.dumps(tokens.to_dict()).encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> Tokens:
        from cryptography.fernet import InvalidToken

        try:
            raw = self._fernet.decrypt(ciphertext.encode("ascii"))
        except InvalidToken as exc:
            raise DriveNotConnectedError(
                "The stored Google connection could not be read. Connect Google Drive again."
            ) from exc
        return Tokens.from_dict(json.loads(raw))


# --- the Drive API ---------------------------------------------------------
@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str
    modified_time: str = ""
    size: int = 0
    #: Folder path below the chosen folder, for the import report.
    path: str = ""

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME

    @property
    def importable(self) -> bool:
        return self.mime_type in IMPORTABLE_MIMES

    @property
    def exported(self) -> bool:
        return self.mime_type in EXPORT_AS_PDF

    @property
    def why_skipped(self) -> str:
        if self.is_folder or self.importable:
            return ""
        if self.mime_type.startswith("image/") or self.mime_type.startswith("audio/"):
            return "Images and audio need a model to read them; upload them from the Library."
        if self.mime_type in {"application/vnd.ms-powerpoint"}:
            return "Old .ppt format; save it as .pptx or PDF in Drive first."
        return "Not a PDF, PowerPoint, Google Doc or Google Slides file."

    @property
    def imported_filename(self) -> str:
        """The name the document carries; exports become a .pdf."""
        if self.exported:
            return f"{Path(self.name).stem or self.name}.pdf"
        return self.name

    @property
    def imported_media_type(self) -> str:
        return "application/pdf" if self.exported else self.mime_type

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "mime_type": self.mime_type,
            "modified_time": self.modified_time,
            "size": self.size,
            "is_folder": self.is_folder,
            "importable": self.importable,
            "why_skipped": self.why_skipped,
            "path": self.path,
        }


_FILE_FIELDS = "id,name,mimeType,modifiedTime,size"
_FOLDER_URL = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")
_FILE_URL = re.compile(r"/(?:file|document|presentation)/d/([A-Za-z0-9_-]{10,})")
_ID_PARAM = re.compile(r"[?&]id=([A-Za-z0-9_-]{10,})")
_BARE_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")


def folder_id_from(text: str) -> str:
    """Accept a Drive folder id, or any of the URLs Drive shows for one.

    "root" (My Drive) is allowed through as itself.
    """
    candidate = text.strip()
    if candidate in {"", "root"}:
        return "root"
    for pattern in (_FOLDER_URL, _FILE_URL, _ID_PARAM):
        match = pattern.search(candidate)
        if match:
            return match.group(1)
    if _BARE_ID.match(candidate):
        return candidate
    raise DriveError("That does not look like a Google Drive folder link or id.", status=400)


def _file_from(item: dict[str, Any], path: str = "") -> DriveFile:
    return DriveFile(
        id=str(item.get("id", "")),
        name=str(item.get("name", "")) or "Untitled",
        mime_type=str(item.get("mimeType", "")),
        modified_time=str(item.get("modifiedTime", "")),
        size=int(item.get("size") or 0),
        path=path,
    )


@dataclass
class DriveListing:
    folder: DriveFile
    files: list[DriveFile] = field(default_factory=list)
    #: The parent folder's id, when the chosen one has a single parent.
    parent_id: str = ""


class DriveClient:
    """Calls to Drive on behalf of one connected account."""

    def __init__(self, http: httpx.Client, access_token: str) -> None:
        self._http = http
        self._headers = {"Authorization": f"Bearer {access_token}"}

    def _get(
        self, path: str, params: dict[str, Any] | None = None, **kwargs: Any
    ) -> httpx.Response:
        try:
            response = self._http.get(
                f"{API_URL}{path}", params=params, headers=self._headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise DriveError(f"Could not reach Google Drive: {exc}") from exc
        if response.status_code == 401:
            raise DriveNotConnectedError(
                "Google no longer accepts this connection. Connect Google Drive again."
            )
        if response.status_code == 404:
            raise DriveError("That folder or file is not in this Google account.", status=404)
        if response.status_code >= 400:
            raise DriveError(
                f"Google Drive answered: {_error_detail(response)}",
                status=502 if response.status_code >= 500 else 400,
            )
        return response

    def account_email(self) -> str:
        data = self._get("/about", {"fields": "user(emailAddress,displayName)"}).json()
        user = data.get("user") or {}
        return str(user.get("emailAddress") or user.get("displayName") or "")

    def file(self, file_id: str) -> DriveFile:
        data = self._get(
            f"/files/{file_id}", {"fields": _FILE_FIELDS, "supportsAllDrives": "true"}
        ).json()
        return _file_from(data)

    def folder(self, folder_id: str) -> tuple[DriveFile, str]:
        """The folder itself and its parent's id (empty at the top)."""
        if folder_id == "root":
            return DriveFile(id="root", name="My Drive", mime_type=FOLDER_MIME), ""
        data = self._get(
            f"/files/{folder_id}",
            {"fields": f"{_FILE_FIELDS},parents", "supportsAllDrives": "true"},
        ).json()
        found = _file_from(data)
        if not found.is_folder:
            raise DriveError("That link points at a file, not a folder.", status=400)
        parents = data.get("parents") or []
        return found, str(parents[0]) if parents else ""

    def list_children(self, folder_id: str, path: str = "") -> list[DriveFile]:
        """Everything directly inside a folder, folders first, by name."""
        items: list[DriveFile] = []
        page_token = ""
        query = f"'{folder_id}' in parents and trashed = false"
        while True:
            params: dict[str, Any] = {
                "q": query,
                "fields": f"nextPageToken,files({_FILE_FIELDS})",
                "pageSize": 200,
                "orderBy": "folder,name",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._get("/files", params).json()
            items.extend(_file_from(item, path) for item in data.get("files", []))
            page_token = str(data.get("nextPageToken") or "")
            if not page_token:
                return items

    def listing(self, folder_id: str) -> DriveListing:
        folder, parent_id = self.folder(folder_id)
        return DriveListing(folder=folder, files=self.list_children(folder.id), parent_id=parent_id)

    def walk(self, folder_id: str, *, recursive: bool, max_files: int = 500) -> list[DriveFile]:
        """Importable files under a folder, depth first, bounded."""
        found: list[DriveFile] = []
        pending: list[tuple[str, str]] = [(folder_id, "")]
        while pending and len(found) < max_files:
            current, path = pending.pop(0)
            for item in self.list_children(current, path):
                if item.is_folder:
                    if recursive:
                        pending.append((item.id, f"{path}/{item.name}" if path else item.name))
                    continue
                found.append(item)
                if len(found) >= max_files:
                    break
        return found

    def fetch(self, file: DriveFile, *, max_bytes: int) -> bytes:
        """The file's bytes, exported as PDF for Google-native formats.

        Read once and returned; the caller indexes and discards them.
        """
        if file.size and file.size > max_bytes:
            raise DriveError(
                f"{file.name} is larger than the {max_bytes // (1024 * 1024)} MB limit.",
                status=400,
            )
        if file.exported:
            response = self._get(f"/files/{file.id}/export", {"mimeType": "application/pdf"})
        else:
            response = self._get(f"/files/{file.id}", {"alt": "media", "supportsAllDrives": "true"})
        data = response.content
        if len(data) > max_bytes:
            raise DriveError(
                f"{file.name} is larger than the {max_bytes // (1024 * 1024)} MB limit.",
                status=400,
            )
        if not data:
            raise DriveError(f"{file.name} came back empty from Google Drive.", status=502)
        return data
