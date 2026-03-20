"""Centralized logging configuration for the agent process."""

from __future__ import annotations

import json
import logging
import logging.handlers
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.settings_models import AppSettings, LoggingSettings


def _parse_level(name: str, default: int = logging.INFO) -> int:
    level = getattr(logging, name.upper(), None)
    return int(level) if isinstance(level, int) else default


def _resolve_threshold(
    logger_name: str, base_level: int, subsystem_levels: dict[str, int]
) -> int:
    """Minimum numeric level required for a record from this logger to pass the filter."""
    best_key = ""
    best_level = base_level
    for key, lvl in subsystem_levels.items():
        if logger_name == key or logger_name.startswith(f"{key}."):
            if len(key) > len(best_key):
                best_key = key
                best_level = lvl
    return best_level


def _subsystem_allowed(logger_name: str, allowed_prefixes: list[str]) -> bool:
    if not allowed_prefixes:
        return True
    for p in allowed_prefixes:
        if logger_name == p or logger_name.startswith(f"{p}."):
            return True
    return False


class SubsystemFilter(logging.Filter):
    """Per-logger minimum level (longest prefix in subsystem map) plus optional console allowlist."""

    def __init__(
        self,
        base_level: int,
        subsystem_levels: dict[str, int],
        allowed_subsystems: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.base_level = base_level
        self.subsystem_levels = subsystem_levels
        self.allowed_subsystems = allowed_subsystems

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        required = _resolve_threshold(name, self.base_level, self.subsystem_levels)
        if record.levelno < required:
            return False
        if self.allowed_subsystems:
            return _subsystem_allowed(name, self.allowed_subsystems)
        return True


class JsonFormatter(logging.Formatter):
    """Single-line JSON for console or file (observability pipelines)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        meta = getattr(record, "_meta", None)
        if meta is not None:
            payload["meta"] = meta
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


@dataclass(frozen=True)
class _LoggingResolution:
    """Snapshot used by SubsystemLogger.is_enabled after setup_logging."""

    file_base: int
    console_base: int | None
    log_to_console: bool
    subsystem_levels: dict[str, int]
    console_subsystems: list[str]


_resolution: _LoggingResolution | None = None
_resolution_lock = threading.Lock()


def _set_resolution(r: _LoggingResolution | None) -> None:
    global _resolution
    with _resolution_lock:
        _resolution = r


def get_logging_resolution() -> _LoggingResolution | None:
    with _resolution_lock:
        return _resolution


_transports: list[Callable[[logging.LogRecord], None]] = []
_transports_lock = threading.Lock()


class _TransportDispatchHandler(logging.Handler):
    """Dispatches each record to registered transports; never blocks logging on transport errors."""

    def emit(self, record: logging.LogRecord) -> None:
        with _transports_lock:
            callbacks = list(_transports)
        for fn in callbacks:
            try:
                fn(record)
            except Exception:
                self.handleError(record)


_transport_handler_singleton: _TransportDispatchHandler | None = None


def _transport_handler() -> _TransportDispatchHandler:
    global _transport_handler_singleton
    if _transport_handler_singleton is None:
        _transport_handler_singleton = _TransportDispatchHandler(level=logging.DEBUG)
    return _transport_handler_singleton


def register_log_transport(
    fn: Callable[[logging.LogRecord], None],
) -> Callable[[], None]:
    """Register a sink for every log record. Returns unregister callable (OTLP, custom exporters)."""

    with _transports_lock:
        _transports.append(fn)
    root = logging.getLogger()
    th = _transport_handler()
    if th not in root.handlers:
        root.addHandler(th)

    def unregister() -> None:
        with _transports_lock:
            try:
                _transports.remove(fn)
            except ValueError:
                pass

    return unregister


class SubsystemLogger:
    """Logger wrapper: child(), is_enabled(), optional meta= on level methods."""

    __slots__ = ("_logger", "_subsystem")

    def __init__(self, logger: logging.Logger, subsystem: str) -> None:
        self._logger = logger
        self._subsystem = subsystem

    @property
    def subsystem(self) -> str:
        return self._subsystem

    @property
    def unwrap(self) -> logging.Logger:
        return self._logger

    def child(self, name: str) -> SubsystemLogger:
        return SubsystemLogger(self._logger.getChild(name), f"{self._subsystem}.{name}")

    def is_enabled(self, level: str, target: str = "any") -> bool:
        """Whether a log at *level* would be emitted for *target* (any|console|file)."""
        levelno = _parse_level(level, logging.INFO)
        res = get_logging_resolution()
        if res is None:
            return levelno >= logging.INFO

        name = self._logger.name

        def file_ok() -> bool:
            ft = _resolve_threshold(name, res.file_base, res.subsystem_levels)
            return levelno >= ft

        def console_ok() -> bool:
            if not res.log_to_console or res.console_base is None:
                return False
            ct = _resolve_threshold(name, res.console_base, res.subsystem_levels)
            if levelno < ct:
                return False
            return _subsystem_allowed(name, res.console_subsystems)

        if target == "file":
            return file_ok()
        if target == "console":
            return console_ok()
        return file_ok() or console_ok()

    def _merge_extra(self, meta: dict[str, Any] | None, kwargs: dict[str, Any]) -> None:
        extra = dict(kwargs.pop("extra", None) or {})
        if meta is not None:
            extra["_meta"] = meta
        if extra:
            kwargs["extra"] = extra

    def debug(
        self, msg: Any, *args: Any, meta: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self._merge_extra(meta, kwargs)
        self._logger.debug(msg, *args, **kwargs)

    def info(
        self, msg: Any, *args: Any, meta: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self._merge_extra(meta, kwargs)
        self._logger.info(msg, *args, **kwargs)

    def warning(
        self, msg: Any, *args: Any, meta: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self._merge_extra(meta, kwargs)
        self._logger.warning(msg, *args, **kwargs)

    def error(
        self, msg: Any, *args: Any, meta: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        self._merge_extra(meta, kwargs)
        self._logger.error(msg, *args, **kwargs)

    def exception(
        self, msg: Any, *args: Any, meta: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        kwargs.setdefault("exc_info", True)
        self._merge_extra(meta, kwargs)
        self._logger.exception(msg, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._logger, name)


def create_subsystem_logger(subsystem: str) -> SubsystemLogger:
    """Return a SubsystemLogger for the given dotted logger name (e.g. ext.memory)."""
    return SubsystemLogger(logging.getLogger(subsystem), subsystem)


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


def _build_subsystem_level_map(cfg: LoggingSettings) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, val in cfg.subsystems.items():
        out[key] = _parse_level(str(val), logging.INFO)
    return out


def setup_logging(project_root: Path, settings: AppSettings) -> None:
    """Configure root logger: rotating file, optional console, subsystem filters, transports."""
    cfg = settings.logging
    root_level = _parse_level(cfg.level, logging.INFO)
    console_level_name = cfg.console_level or cfg.level
    console_base = _parse_level(console_level_name, logging.INFO)
    subsystem_levels = _build_subsystem_level_map(cfg)
    allowed_console = list(cfg.console_subsystems) if cfg.console_subsystems else []

    for sub_name, sub_lvl in subsystem_levels.items():
        logging.getLogger(sub_name).setLevel(sub_lvl)

    text_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    json_formatter = JsonFormatter()

    file_fmt = json_formatter if cfg.file_style == "json" else text_formatter
    console_fmt = json_formatter if cfg.console_style == "json" else text_formatter

    root = logging.getLogger()
    root.setLevel(root_level)
    for h in root.handlers[:]:
        root.removeHandler(h)

    file_handler = _file_handler(project_root, cfg, logging.DEBUG)
    file_handler.addFilter(
        SubsystemFilter(root_level, subsystem_levels, allowed_subsystems=None)
    )
    file_handler.setFormatter(file_fmt)
    root.addHandler(file_handler)

    if cfg.log_to_console:
        console_handler = _console_handler(logging.DEBUG)
        console_handler.addFilter(
            SubsystemFilter(
                console_base,
                subsystem_levels,
                allowed_subsystems=allowed_console if allowed_console else None,
            )
        )
        console_handler.setFormatter(console_fmt)
        root.addHandler(console_handler)

    th = _transport_handler()
    th.setLevel(logging.DEBUG)
    root.addHandler(th)

    _set_resolution(
        _LoggingResolution(
            file_base=root_level,
            console_base=console_base if cfg.log_to_console else None,
            log_to_console=cfg.log_to_console,
            subsystem_levels=subsystem_levels,
            console_subsystems=allowed_console,
        )
    )
