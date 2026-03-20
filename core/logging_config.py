"""Centralized logging configuration for the agent process."""

import logging
import logging.handlers
from pathlib import Path

from core.settings_models import AppSettings, LoggingSettings


def _file_handler(
    project_root: Path, cfg: LoggingSettings, level: int
) -> logging.Handler:
    log_file = cfg.file
    max_bytes = int(cfg.max_bytes)
    backup_count = int(cfg.backup_count)
    log_path = project_root / log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    h.setLevel(level)
    return h


def _console_handler(level: int) -> logging.Handler:
    h = logging.StreamHandler()
    h.setLevel(level)
    return h


def setup_logging(project_root: Path, settings: AppSettings) -> None:
    """Configure the root logger: file handler with optional console output.

    Reads config from settings.logging. Creates log directory
    if needed. By default logs only to file to keep CLI clean.
    """
    cfg = settings.logging
    level_name = cfg.level.upper()
    log_to_console = cfg.log_to_console
    level = getattr(logging, level_name, logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        root.removeHandler(h)
    file_handler = _file_handler(project_root, cfg, level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    if log_to_console:
        console_handler = _console_handler(level)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)
