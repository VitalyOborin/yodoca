"""Shared helpers for architecture boundary check scripts."""

from __future__ import annotations

import sys


def is_tty() -> bool:
    return sys.stdout.isatty()


def green(text: str) -> str:
    return f"\033[32m{text}\033[0m" if is_tty() else text


def red(text: str) -> str:
    return f"\033[31m{text}\033[0m" if is_tty() else text


def yellow(text: str) -> str:
    return f"\033[33m{text}\033[0m" if is_tty() else text
