"""Parent-process watchdog for the bundled sidecar.

The desktop shell launches this backend as a child process. Killing that child
is not sufficient to stop it: a one-file PyInstaller build runs a bootloader
process that forks the real interpreter, so terminating the direct child can
leave the worker orphaned — still holding its port and the student's database.

This watchdog closes that gap from the inside. The shell passes its own pid,
and a daemon thread here exits the process as soon as that pid disappears. It
covers every way the parent can go away, including a crash or a force quit,
which signal forwarding alone would not.
"""

from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger(__name__)

#: How often to check. Cheap enough to be frequent, slow enough to be invisible.
POLL_SECONDS = 1.0


def _parent_is_alive(pid: int) -> bool:
    """Whether ``pid`` still exists.

    ``os.kill(pid, 0)`` performs the permission and existence check without
    sending a signal. ``PermissionError`` means the process exists but is owned
    by someone else, which still counts as alive.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def watch_parent(pid: int, *, poll_seconds: float = POLL_SECONDS) -> threading.Thread | None:
    """Exit this process when ``pid`` is gone. Returns the watchdog thread."""
    if pid <= 1:
        return None
    if not _parent_is_alive(pid):
        # The parent died before we finished starting; do not hold the port.
        log.warning("parent_already_gone", extra={"parent_pid": pid})
        os._exit(0)

    def _watch() -> None:
        while True:
            time.sleep(poll_seconds)
            if not _parent_is_alive(pid):
                log.info("parent_exited_shutting_down", extra={"parent_pid": pid})
                # os._exit rather than sys.exit: this runs on a daemon thread,
                # where raising SystemExit would only end the thread.
                os._exit(0)

    thread = threading.Thread(target=_watch, name="parent-watchdog", daemon=True)
    thread.start()
    log.info("watching_parent", extra={"parent_pid": pid})
    return thread


def watch_parent_from_env() -> threading.Thread | None:
    """Start the watchdog if the launcher supplied its pid."""
    raw = os.environ.get("STUDYPILOT_PARENT_PID", "").strip()
    if not raw:
        return None
    try:
        pid = int(raw)
    except ValueError:
        log.warning("invalid_parent_pid", extra={"value": raw})
        return None
    return watch_parent(pid)
