"""Provider API keys entered in the desktop app.

The hosted deployment gets its keys from the operator's environment and
nothing here applies to it. The desktop app has no environment to speak of —
nobody launches a packaged `.app` with variables set — so the key the user
types into Settings has to live on this Mac. It lives in the **macOS
Keychain**: encrypted at rest, unlocked with the user's login, the same store
Safari and Mail use, and readable only by this user's processes. Nothing is
written to a plain file.

Design points:

* The Keychain is reached through `keyring`, which also gives a working
  store on Linux (Secret Service) for development. Where no backend works —
  a headless CI box, a locked-down account — the module falls back to a
  file with mode 0600 in the app's data directory and says so in the log,
  rather than silently losing the key.
* At startup the stored keys are loaded into the process environment, so
  every provider keeps reading ``os.environ`` and knows nothing about this
  module. A key set through the API is written to both at once.
* Values are never returned by the API; it reports which providers have a
  key and nothing else.
* Keys are read from the environment first: a value set there wins, and is
  reported as "set by the environment" so the UI does not offer to delete it.
* A `provider_keys.json` left by an earlier build is migrated into the
  Keychain on first load and deleted.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import stat
from pathlib import Path

from app.config import Settings

log = logging.getLogger(__name__)

#: The legacy file store, kept only as a fallback and for migration.
FILENAME = "provider_keys.json"
#: Keychain service name; the account is the provider key.
SERVICE = "StudyPilot"

#: Provider key → the environment variable the provider reads.
ENV_FOR_PROVIDER: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    # The Google Cloud OAuth client the desktop identifies itself with when
    # connecting Google Drive. Not a model provider, but the same shape: a
    # value typed once in Settings, kept in the Keychain, read from the
    # environment by the code that needs it.
    "google_client_id": "GOOGLE_OAUTH_CLIENT_ID",
    "google_client_secret": "GOOGLE_OAUTH_CLIENT_SECRET",
}
#: Entries of `ENV_FOR_PROVIDER` that are credentials for an integration
#: rather than a model provider; the UI groups them separately.
INTEGRATION_KEYS = frozenset({"google_client_id", "google_client_secret"})


# --- the secure store ------------------------------------------------------
class _KeychainStore:
    """`keyring` behind a tiny interface, so tests can swap it for memory."""

    def __init__(self) -> None:
        self._keyring = None
        self._broken = False

    def _backend(self):
        if self._keyring is None and not self._broken:
            try:
                import keyring
                from keyring.backends.fail import Keyring as FailKeyring

                backend = keyring.get_keyring()
                if isinstance(backend, FailKeyring):
                    raise RuntimeError("no usable keyring backend")
                self._keyring = keyring
            except Exception as exc:
                self._broken = True
                log.warning("keychain_unavailable", extra={"detail": str(exc)})
        return self._keyring

    @property
    def available(self) -> bool:
        return self._backend() is not None

    @property
    def name(self) -> str:
        backend = self._backend()
        if backend is None:
            return "file"
        return type(backend.get_keyring()).__module__.rsplit(".", 1)[-1]

    def get(self, provider: str) -> str | None:
        backend = self._backend()
        if backend is None:
            return None
        try:
            return backend.get_password(SERVICE, provider) or None
        except Exception as exc:
            log.warning("keychain_read_failed", extra={"provider": provider, "detail": str(exc)})
            return None

    def set(self, provider: str, value: str) -> bool:
        backend = self._backend()
        if backend is None:
            return False
        try:
            backend.set_password(SERVICE, provider, value)
            return True
        except Exception as exc:
            log.warning("keychain_write_failed", extra={"provider": provider, "detail": str(exc)})
            return False

    def delete(self, provider: str) -> None:
        backend = self._backend()
        if backend is None:
            return
        # Not present, or a backend quirk: either way there is nothing left.
        with contextlib.suppress(Exception):
            backend.delete_password(SERVICE, provider)


class MemoryStore:
    """An in-process store with the same interface, for tests."""

    available = True
    name = "memory"

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, provider: str) -> str | None:
        return self.values.get(provider)

    def set(self, provider: str, value: str) -> bool:
        self.values[provider] = value
        return True

    def delete(self, provider: str) -> None:
        self.values.pop(provider, None)


#: Swapped by tests; otherwise the Keychain.
store: _KeychainStore | MemoryStore = _KeychainStore()


def storage_name() -> str:
    """Where keys are kept on this machine, for the UI and the startup log."""
    return "keychain" if store.name == "macOS" else store.name


# --- the legacy file ---------------------------------------------------------
def _path(settings: Settings) -> Path:
    return settings.data_dir / FILENAME


def _read_file(settings: Settings) -> dict[str, str]:
    path = _path(settings)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("provider_keys_unreadable", extra={"detail": str(exc)})
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v}


def _write_file(settings: Settings, keys: dict[str, str]) -> None:
    path = _path(settings)
    settings.ensure_dirs()
    if keys:
        path.write_text(json.dumps(keys, indent=2), encoding="utf-8")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    elif path.exists():
        path.unlink()


def _migrate_file(settings: Settings) -> None:
    """Move a legacy file's keys into the Keychain and delete the file."""
    legacy = _read_file(settings)
    if not legacy or not store.available:
        return
    moved = [provider for provider, value in legacy.items() if store.set(provider, value)]
    if len(moved) == len(legacy):
        _path(settings).unlink(missing_ok=True)
        log.info("provider_keys_migrated_to_keychain", extra={"providers": moved})


def _stored(settings: Settings, provider: str) -> str | None:
    if store.available:
        return store.get(provider)
    return _read_file(settings).get(provider)


# --- the API this module presents -------------------------------------------
def load_into_environment(settings: Settings) -> list[str]:
    """Export stored keys that the environment does not already set.

    Returns the providers whose key came from the store, for the startup log.
    """
    if settings.is_hosted:
        return []
    _migrate_file(settings)
    loaded: list[str] = []
    for provider, variable in ENV_FOR_PROVIDER.items():
        if os.environ.get(variable):
            continue
        value = _stored(settings, provider)
        if value:
            os.environ[variable] = value
            loaded.append(provider)
    return loaded


def status(settings: Settings) -> dict[str, dict[str, bool]]:
    """Which providers have a key, and whether it is the environment's."""
    out: dict[str, dict[str, bool]] = {}
    for provider, variable in ENV_FOR_PROVIDER.items():
        in_env = bool(os.environ.get(variable))
        stored = not settings.is_hosted and bool(_stored(settings, provider))
        out[provider] = {
            "configured": in_env or stored,
            # True when the value was set outside the app and cannot be
            # removed from inside it.
            "from_environment": in_env and not stored,
        }
    return out


def set_key(settings: Settings, provider: str, value: str) -> None:
    variable = ENV_FOR_PROVIDER[provider]
    value = value.strip()
    if not value:
        raise ValueError("The key is empty.")
    if any(ch.isspace() for ch in value):
        raise ValueError("The key contains whitespace; check that it was copied whole.")
    if not store.set(provider, value):
        keys = _read_file(settings)
        keys[provider] = value
        _write_file(settings, keys)
    os.environ[variable] = value
    _forget_cached_providers()


def delete_key(settings: Settings, provider: str) -> None:
    variable = ENV_FOR_PROVIDER[provider]
    had = _stored(settings, provider) is not None
    store.delete(provider)
    keys = _read_file(settings)
    if provider in keys:
        del keys[provider]
        _write_file(settings, keys)
    if had:
        # Only drop the environment value if it was ours to set.
        os.environ.pop(variable, None)
    _forget_cached_providers()


def _forget_cached_providers() -> None:
    """Providers capture the key at construction; make the next use rebuild."""
    from app.models.llm import cached_llm

    cached_llm.cache_clear()


# --- opaque secrets that are not provider keys ---------------------------------
def get_secret(settings: Settings, name: str) -> str | None:
    """A stored value that is not a provider key (e.g. an encryption key)."""
    return _stored(settings, name)


def set_secret(settings: Settings, name: str, value: str) -> None:
    if not store.set(name, value):
        keys = _read_file(settings)
        keys[name] = value
        _write_file(settings, keys)


def delete_secret(settings: Settings, name: str) -> None:
    store.delete(name)
    keys = _read_file(settings)
    if name in keys:
        del keys[name]
        _write_file(settings, keys)
