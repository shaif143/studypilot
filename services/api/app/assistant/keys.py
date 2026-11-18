"""The Claude API key an account uses for the chat.

On a hosted site every account brings its own key. The operator's
``ANTHROPIC_API_KEY`` is deliberately not a fallback there: the chat can run
the most expensive model for many steps, and anyone who can sign up would be
spending the operator's money. On a single-user installation there is nobody
else, so the key already set for the rest of the app is used when the profile
has not saved one of its own.

A saved key is encrypted before it reaches the database, with the same kind of
key the stored Google tokens use: derived from an operator secret when hosted,
generated into the Keychain on a Mac. The value is never returned by the API;
the screen is told that a key exists and its last four characters.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

from app import secrets
from app.config import Settings
from app.storage.repositories import AssistantRepository

#: Keychain entry (single-user installations) holding the encryption key.
KEY_NAME = "assistant_key_cipher"
#: Where a key comes from, as the screen is told.
SOURCE_ACCOUNT = "account"
SOURCE_INSTALLATION = "installation"


class KeyStorageError(RuntimeError):
    """A key cannot be stored or read here; the message is for the user."""


@dataclass(frozen=True)
class ResolvedKey:
    value: str
    #: "account" (saved in the chat) or "installation" (this machine's own key).
    source: str
    hint: str


def _fernet(settings: Settings):
    from cryptography.fernet import Fernet

    if settings.is_hosted:
        secret = settings.token_encryption_key or settings.jwt_secret
        if not secret:
            raise KeyStorageError(
                "This site cannot store a key yet: its operator has to set "
                "STUDYPILOT_TOKEN_ENCRYPTION_KEY first."
            )
        # A label of its own, so this is not the key that opens Drive tokens.
        digest = hashlib.sha256(b"studypilot-assistant-key:" + secret.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))
    key = secrets.get_secret(settings, KEY_NAME)
    if not key:
        key = Fernet.generate_key().decode("ascii")
        secrets.set_secret(settings, KEY_NAME, key)
    return Fernet(key.encode("ascii"))


def clean(value: str) -> str:
    """The key as typed, or a refusal that says what is wrong with it."""
    value = value.strip()
    if not value:
        raise ValueError("The key is empty.")
    if any(ch.isspace() for ch in value):
        raise ValueError("The key contains whitespace; check that it was copied whole.")
    if not value.startswith("sk-ant-"):
        raise ValueError(
            "That does not look like a Claude API key. They start with sk-ant- and come "
            "from the Claude Console, under API keys."
        )
    if len(value) > 400:
        raise ValueError("That is too long to be a Claude API key.")
    return value


def save(repo: AssistantRepository, settings: Settings, user_id: str, value: str) -> str:
    """Encrypt and store an account's key; returns the hint shown for it."""
    value = clean(value)
    ciphertext = _fernet(settings).encrypt(value.encode("utf-8")).decode("ascii")
    hint = value[-4:]
    repo.save_key(user_id, ciphertext, hint)
    return hint


def delete(repo: AssistantRepository, user_id: str) -> None:
    repo.delete_key(user_id)


def resolve(repo: AssistantRepository, settings: Settings, user_id: str) -> ResolvedKey | None:
    """The key this account's chat runs on, or ``None`` when there is none."""
    row = repo.key(user_id)
    if row is not None:
        from cryptography.fernet import InvalidToken

        try:
            value = _fernet(settings).decrypt(row.ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, KeyStorageError):
            # The secret that encrypted it has changed. The stored value is
            # unreadable for good, so say there is no key and let it be typed again.
            value = ""
        if value:
            return ResolvedKey(value=value, source=SOURCE_ACCOUNT, hint=row.hint)
    if not settings.is_hosted:
        value = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if value:
            return ResolvedKey(value=value, source=SOURCE_INSTALLATION, hint=value[-4:])
    return None


def seal(settings: Settings, value: str) -> str:
    """Encrypt another secret of the chat's (a connector's token) the same way."""
    return _fernet(settings).encrypt(value.encode("utf-8")).decode("ascii")


def unseal(settings: Settings, ciphertext: str) -> str:
    """The secret back, or "" when it can no longer be read."""
    if not ciphertext:
        return ""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet(settings).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, KeyStorageError):
        return ""
