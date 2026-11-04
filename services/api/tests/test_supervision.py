"""The parent-process watchdog.

This exists because of a real bug: quitting the packaged macOS app left the
backend running as an orphan, still serving on its port. Tauri kills the direct
child, but a one-file PyInstaller build forks a worker that survived.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

from app.supervision import _parent_is_alive, watch_parent, watch_parent_from_env


def test_detects_a_live_process():
    assert _parent_is_alive(os.getpid()) is True


def test_detects_a_dead_process():
    # Spawn and reap a trivial process, then check its pid is seen as gone.
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert _parent_is_alive(child.pid) is False


def test_init_is_treated_as_alive_despite_permissions():
    """pid 1 is owned by root; a PermissionError must not read as 'dead'."""
    assert _parent_is_alive(1) is True


def test_watchdog_is_not_started_for_an_implausible_pid():
    assert watch_parent(0) is None
    assert watch_parent(1) is None


def test_watchdog_ignores_a_missing_or_invalid_environment_value(monkeypatch):
    monkeypatch.delenv("STUDYPILOT_PARENT_PID", raising=False)
    assert watch_parent_from_env() is None

    monkeypatch.setenv("STUDYPILOT_PARENT_PID", "not-a-number")
    assert watch_parent_from_env() is None


def test_watchdog_thread_starts_for_a_live_parent():
    thread = watch_parent(os.getpid(), poll_seconds=0.05)
    assert thread is not None
    assert thread.is_alive()
    # A daemon thread must not keep the interpreter alive at shutdown.
    assert thread.daemon is True


def test_child_exits_when_its_parent_disappears(tmp_path):
    """End-to-end: the behaviour the packaged app depends on.

    A grandchild process watches a parent that is killed; it must exit by
    itself rather than lingering.
    """
    repo_root = os.fspath(tmp_path)
    # The spawned process needs the backend package on its path.
    api_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = tmp_path / "watched.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os, sys, time
            sys.path.insert(0, {api_root!r})
            from app.supervision import watch_parent

            parent = int(sys.argv[1])
            watch_parent(parent, poll_seconds=0.05)
            open(os.path.join({repo_root!r}, "started"), "w").close()
            # Without the watchdog this would outlive the parent indefinitely.
            time.sleep(30)
            """
        ),
        encoding="utf-8",
    )

    # A stand-in "shell" process for the watched child to observe.
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    watched = subprocess.Popen([sys.executable, os.fspath(script), str(parent.pid)])

    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (tmp_path / "started").exists():
            time.sleep(0.05)
        assert (tmp_path / "started").exists(), "the watched process never started"

        parent.kill()
        parent.wait(timeout=5)

        # The watched process must exit on its own, without being signalled.
        assert watched.wait(timeout=10) == 0
    finally:
        for process in (parent, watched):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
