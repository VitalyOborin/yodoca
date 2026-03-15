"""Web channel: HTTP API for OpenAI-compatible frontends (FastAPI/uvicorn)."""

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sandbox.extensions.web_channel.app import create_app
from sandbox.extensions.web_channel.bridge import RequestBridge

if TYPE_CHECKING:
    from core.events.models import Event
    from core.extensions.context import ExtensionContext

logger = logging.getLogger(__name__)


class WebChannelExtension:
    """ChannelProvider + StreamingChannelProvider + ServiceProvider for HTTP API."""

    def __init__(self) -> None:
        self._context: ExtensionContext | None = None
        self._config: dict[str, Any] = {}
        self._bridge: RequestBridge | None = None
        self._scheduler: Any = None
        self._inbox: Any = None
        self._request_logger: logging.Logger | None = None
        self._app: Any = None
        self._server: Any = None
        self._server_task: asyncio.Task[Any] | None = None
        self._channel_id = "web_channel"
        self._inbox_stream_queues: dict[
            asyncio.Queue[dict[str, Any]], asyncio.AbstractEventLoop
        ] = {}

    def _setup_request_logger(self, log_file: str) -> logging.Logger:
        """Configure a dedicated file logger for web_channel HTTP audit events."""
        logger_name = "ext.web_channel.http_audit"
        request_logger = logging.getLogger(logger_name)
        request_logger.setLevel(logging.INFO)
        request_logger.propagate = False

        abs_path = (Path(__file__).resolve().parents[3] / log_file).resolve()
        abs_path.parent.mkdir(parents=True, exist_ok=True)

        handler_exists = any(
            isinstance(handler, logging.FileHandler)
            and Path(getattr(handler, "baseFilename", "")).resolve() == abs_path
            for handler in request_logger.handlers
        )
        if not handler_exists:
            file_handler = logging.FileHandler(abs_path, encoding="utf-8")
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s - %(message)s",
                    "%Y-%m-%d %H:%M:%S",
                )
            )
            request_logger.addHandler(file_handler)
        return request_logger

    async def initialize(self, context: "ExtensionContext") -> None:
        self._context = context
        self._scheduler = None
        self._inbox = None
        self._config = {
            "host": context.get_config("host", "127.0.0.1"),
            "port": context.get_config("port", 8080),
            "api_key": context.get_config("api_key", ""),
            "cors_origins": context.get_config("cors_origins", ["*"]),
            "request_timeout_seconds": context.get_config(
                "request_timeout_seconds", 120
            ),
            "model_name": context.get_config("model_name", "yodoca"),
            "default_user_id": context.get_config("default_user_id", "web_user"),
        }
        log_file = context.get_config("log_file", "sandbox/logs/web.log")
        self._request_logger = self._setup_request_logger(log_file)
        api_key = self._config.get("api_key")
        if not api_key:
            try:
                api_key = await context.get_secret("web_channel.api_key")
                if api_key:
                    self._config["api_key"] = api_key
            except Exception:
                pass
        if not self._config.get("api_key"):
            logger.warning(
                "web_channel: no api_key configured; authentication disabled"
            )
        self._bridge = RequestBridge(
            request_timeout_seconds=float(
                self._config.get("request_timeout_seconds", 120)
            )
        )
        context.subscribe_event("inbox.item.ingested", self._on_inbox_item_ingested)
        self._app = create_app(self)

    async def _on_inbox_item_ingested(self, event: "Event") -> None:
        """Forward inbox ingestion events to SSE subscribers."""
        payload = dict(event.payload or {})
        payload["event"] = "inbox.item.ingested"
        self.push_inbox_stream_event(payload)

    def get_scheduler(self) -> Any | None:
        """Resolve scheduler extension lazily. Returns None when unavailable."""
        if self._scheduler is not None:
            return self._scheduler
        if not self._context:
            return None
        try:
            self._scheduler = self._context.get_extension("scheduler")
        except Exception:
            self._scheduler = None
        return self._scheduler

    def get_inbox(self) -> Any | None:
        """Resolve inbox extension lazily. Returns None when unavailable."""
        if self._inbox is not None:
            return self._inbox
        if not self._context:
            return None
        try:
            self._inbox = self._context.get_extension("inbox")
        except Exception:
            self._inbox = None
        return self._inbox

    async def start(self) -> None:
        pass

    async def run_background(self) -> None:
        """Run uvicorn HTTP server. Handles CancelledError and OSError on bind."""
        import uvicorn

        host = self._config.get("host", "127.0.0.1")
        port = int(self._config.get("port", 8080))
        config = uvicorn.Config(
            self._app,
            host=host,
            port=port,
            loop="none",
            log_level="warning",
        )
        server = uvicorn.Server(config)
        try:
            await server.serve()
        except asyncio.CancelledError:
            logger.info("web_channel: uvicorn shutdown requested")
        except OSError as e:
            logger.error("web_channel: failed to bind %s:%s: %s", host, port, e)

    async def stop(self) -> None:
        if self._server_task and not self._server_task.done():
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass

    async def destroy(self) -> None:
        self._inbox_stream_queues.clear()

    def health_check(self) -> bool:
        return self._bridge is not None

    # --- ChannelProvider ---

    async def send_to_user(self, user_id: str, message: str) -> None:
        if self._bridge:
            self._bridge.resolve_response(message)

    async def send_message(self, message: str) -> None:
        if self._bridge:
            self._bridge.push_notification(message)

    # --- StreamingChannelProvider ---

    async def on_stream_start(self, user_id: str) -> None:
        if self._bridge:
            self._bridge.push_stream_event("start", None)

    async def on_stream_chunk(self, user_id: str, chunk: str) -> None:
        if self._bridge:
            self._bridge.push_stream_event("chunk", chunk)

    async def on_stream_status(self, user_id: str, status: str) -> None:
        if self._bridge:
            self._bridge.push_stream_event("status", status)

    async def on_stream_end(self, user_id: str, full_text: str) -> None:
        if self._bridge:
            self._bridge.push_stream_event("end", full_text)
            self._bridge.push_stream_end(full_text)

    def create_inbox_stream_queue(self) -> asyncio.Queue[dict[str, Any]]:
        """Create and register queue for /api/inbox/stream SSE."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._inbox_stream_queues[queue] = asyncio.get_running_loop()
        return queue

    def remove_inbox_stream_queue(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove queue from inbox SSE subscribers."""
        self._inbox_stream_queues.pop(queue, None)

    def push_inbox_stream_event(self, payload: dict[str, Any]) -> None:
        """Broadcast event payload to all active inbox SSE subscribers."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        for queue, loop in tuple(self._inbox_stream_queues.items()):
            if current_loop is loop:
                queue.put_nowait(payload)
            else:
                loop.call_soon_threadsafe(queue.put_nowait, payload)
