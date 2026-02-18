"""Supervisor: spawns and monitors the AI agent process, supports restart-by-signal and auto-restart on crash."""

import os
import signal
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

from dotenv import load_dotenv

from core.settings import get_setting, load_settings

# Project root: parent of supervisor package
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

_settings = load_settings()
_RESTART_FILE = _PROJECT_ROOT / get_setting(
    _settings, "supervisor.restart_file", "sandbox/.restart_requested"
)
_POLL_INTERVAL_SEC = get_setting(
    _settings, "supervisor.restart_file_check_interval", 5
)
_AGENT_CMD = [sys.executable, "-m", "core"]
_MAX_RESTARTS = int(os.environ.get("SUPERVISOR_MAX_RESTARTS", "5"))
_RESTART_WINDOW_MINUTES = float(os.environ.get("SUPERVISOR_RESTART_WINDOW_MINUTES", "5"))


def _log(message: str) -> None:
    """Print to stderr with [supervisor] prefix."""
    print(f"[supervisor] {message}", file=sys.stderr, flush=True)


def _spawn_agent() -> subprocess.Popen[bytes]:
    """Spawn the AI agent as a subprocess."""
    _log("Spawning agent process...")
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.Popen(
        _AGENT_CMD,
        cwd=str(_PROJECT_ROOT),
        env=env,
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    return proc


def _check_restart_requested() -> bool:
    """Check if restart file exists; if so, remove it and return True."""
    if _RESTART_FILE.exists():
        try:
            _RESTART_FILE.unlink()
            return True
        except OSError:
            return True  # Assume restart requested even if unlink failed
    return False


def _count_recent_crashes(crash_times: deque[float], window_minutes: float) -> int:
    """Count crashes within the time window."""
    cutoff = time.monotonic() - (window_minutes * 60)
    while crash_times and crash_times[0] < cutoff:
        crash_times.popleft()
    return len(crash_times)


def main() -> None:
    """Main supervisor loop: spawn agent, poll for restart/crash, restart when needed."""
    crash_times: deque[float] = deque(maxlen=_MAX_RESTARTS + 1)
    restart_requested = False
    shutdown_requested = False

    def on_signal(signum: int, frame: object) -> None:
        nonlocal shutdown_requested
        _log("Shutdown requested.")
        shutdown_requested = True

    signal.signal(signal.SIGINT, on_signal)
    try:
        signal.signal(signal.SIGTERM, on_signal)
    except (ValueError, OSError):
        pass  # SIGTERM not available on Windows

    while True:
        child = _spawn_agent()

        while True:
            time.sleep(_POLL_INTERVAL_SEC)

            if shutdown_requested:
                _log("Terminating agent...")
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                _log("Goodbye.")
                sys.exit(0)

            if _check_restart_requested():
                restart_requested = True
                _log("Restart requested by agent. Terminating...")
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                break

            if child.poll() is not None:
                code = child.returncode or -1
                if code != 0:
                    crash_times.append(time.monotonic())
                    count = _count_recent_crashes(crash_times, _RESTART_WINDOW_MINUTES)
                    if count >= _MAX_RESTARTS:
                        _log(
                            f"Agent crashed {count} times in {_RESTART_WINDOW_MINUTES} minutes. "
                            "Stopping to prevent infinite restart loop."
                        )
                        sys.exit(1)
                    _log(
                        f"Agent exited with code {code}. Restarting ({count}/{_MAX_RESTARTS})..."
                    )
                else:
                    _log("Agent exited normally.")
                break

        if (
            not restart_requested
            and child.poll() is not None
            and (child.returncode or 0) == 0
        ):
            _log("Goodbye.")
            sys.exit(0)

        restart_requested = False
