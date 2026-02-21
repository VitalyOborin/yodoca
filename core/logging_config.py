"""Centralized logging configuration for the agent process."""

import logging
import logging.handlers
from pathlib import Path
from typing import Any


def setup_logging(project_root: Path, settings: dict[str, Any]) -> None:
    """Configure the root logger: file handler with optional console output.

    Reads config from settings.get("logging", {}). Creates log directory
    if needed. By default logs only to file to keep CLI clean.
    """
    cfg = settings.get("logging", {})
    log_file = cfg.get("file", "sandbox/logs/app.log")
    level_name = cfg.get("level", "INFO").upper()
    log_to_console = cfg.get("log_to_console", False)
    max_bytes = int(cfg.get("max_bytes", 10 * 1024 * 1024))  # 10 MB
    backup_count = int(cfg.get("backup_count", 3))

    log_path = project_root / log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, level_name, logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers to avoid duplicates on re-init
    for h in root.handlers[:]:
        root.removeHandler(h)

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if log_to_console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)
