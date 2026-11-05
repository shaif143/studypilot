"""Provider API keys entered in the desktop app.

The hosted deployment gets its keys from the operator's environment and
nothing here applies to it. The desktop app has no environment to speak of —
nobody launches a packaged `.app` with variables set — so the keys the user
types into Settings are kept on this Mac, in the application's own data
directory, in a file only their account can read.

Design points:

* The file is written with mode 0600 and the directory is 0700 already.
* At startup the file is loaded into the process environment, so every
  provider keeps reading ``os.environ`` and knows nothing about this module.
  A key set through the API is written to both at once.
* Values are never returned by the API; it reports which providers have a
  key and nothing else.
* Keys are read from the environment first: a value set there wins, and is
  reported as "set by the environment" so the UI does not offer to delete it.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

from app.config import Settings

log = logging.getLogger(__name__)

FILENAME = "provider_keys.json"

#: Provider key → the environment variable the provider reads.
ENV_FOR_PROVIDER: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def _path(settings: Settings) -> Path:
    return settings.data_dir / FILENAME


def _read(settings: Settings) -> dict[str, str]:
    path = _path(settings)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("provider_keys_unreadable", extra={"detail": str(exc)})
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v}


def _write(settings: Settings, keys: dict[str, str]) -> None:
    path = _path(settings)
    settings.ensure_dirs()
    if keys:
        path.write_text(json.dumps(keys, indent=2), encoding="utf-8")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    elif path.exists():
        path.unlink()


def load_into_environment(settings: Settings) -> list[str]:
    """Export stored keys that the environment does not already set.

    Returns the providers whose key came from the file, for the startup log.
    """
    if settings.is_hosted:
        return []
    loaded: list[str] = []
    for provider, value in _read(settings).items():
        variable = ENV_FOR_PROVIDER.get(provider)
        if variable and not os.environ.get(variable):
            os.environ[variable] = value
            loaded.append(provider)
    return loaded


def status(settings: Settings) -> dict[str, dict[str, bool]]:
    """Which providers have a key, and whether it is the environment's."""
    stored = _read(settings) if not settings.is_hosted else {}
    out: dict[str, dict[str, bool]] = {}
    for provider, variable in ENV_FOR_PROVIDER.items():
        in_env = bool(os.environ.get(variable))
        from_file = provider in stored
        out[provider] = {
            "configured": in_env or from_file,
            # True when the value was set outside the app and cannot be
            # removed from inside it.
            "from_environment": in_env and not from_file,
        }
    return out


def set_key(settings: Settings, provider: str, value: str) -> None:
    variable = ENV_FOR_PROVIDER[provider]
    value = value.strip()
    if not value:
        raise ValueError("The key is empty.")
    if any(ch.isspace() for ch in value):
        raise ValueError("The key contains whitespace; check that it was copied whole.")
    keys = _read(settings)
    keys[provider] = value
    _write(settings, keys)
    os.environ[variable] = value
    _forget_cached_providers()


def delete_key(settings: Settings, provider: str) -> None:
    variable = ENV_FOR_PROVIDER[provider]
    keys = _read(settings)
    if provider in keys:
        del keys[provider]
        _write(settings, keys)
        # Only drop the environment value if it was ours to set.
        os.environ.pop(variable, None)
    _forget_cached_providers()


def _forget_cached_providers() -> None:
    """Providers capture the key at construction; make the next use rebuild."""
    from app.models.llm import cached_llm

    cached_llm.cache_clear()
