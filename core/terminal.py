"""Terminal utilities. Resets terminal state after TUI tools (questionary, prompt_toolkit) exit."""

import sys


def reset_terminal_for_input() -> None:
    """Restore terminal to a state suitable for input() after TUI tools may have left it corrupted.

    Call this when starting a process that will use input() and the previous process
    (e.g. onboarding wizard) used questionary/prompt_toolkit, which can leave echo off
    or the console in raw mode.
    """
    if not sys.stdin.isatty():
        return

    if sys.platform == "win32":
        _reset_windows_console()
    else:
        _reset_unix_terminal()


def _reset_windows_console() -> None:
    """Restore Windows console to line + echo mode (required for input())."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        STD_INPUT_HANDLE = -10
        ENABLE_LINE_INPUT = 0x0002
        ENABLE_ECHO_INPUT = 0x0004
        ENABLE_PROCESSED_INPUT = 0x0001

        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        if handle is None or handle == -1:
            return

        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return

        sane = mode.value | ENABLE_LINE_INPUT | ENABLE_ECHO_INPUT | ENABLE_PROCESSED_INPUT
        kernel32.SetConsoleMode(handle, sane)
    except Exception:
        pass


def _reset_unix_terminal() -> None:
    """Restore Unix terminal to sane state via stty."""
    try:
        import subprocess

        subprocess.run(
            ["stty", "sane"],
            stdin=sys.stdin,
            capture_output=True,
            timeout=2,
        )
    except Exception:
        pass
