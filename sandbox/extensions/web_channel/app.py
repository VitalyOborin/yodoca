"""FastAPI application factory for web_channel."""

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from sandbox.extensions.web_channel.models import ErrorResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Set uptime start when server starts."""
    import sandbox.extensions.web_channel.routes_api as routes_api

    routes_api._start_time = time.monotonic()
    yield


def create_app(extension: Any) -> FastAPI:
    """Create FastAPI app with CORS and auth middleware."""
    app = FastAPI(title="Yodoca Web Channel API", version="0.1.0", lifespan=_lifespan)
    app.state.extension = extension

    cors_origins = extension._config.get("cors_origins", ["*"])
    if isinstance(cors_origins, str):
        cors_origins = [cors_origins]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", "X-Session-Id"],
    )

    api_key = extension._config.get("api_key") or ""

    @app.middleware("http")
    async def request_log_middleware(request: Request, call_next):
        request_logger = getattr(extension, "_request_logger", None)
        request_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        if request_logger:
            request_logger.info(
                "incoming request_id=%s method=%s path=%s client=%s",
                request_id,
                request.method,
                request.url.path,
                request.client.host if request.client else "-",
            )
        try:
            response = await call_next(request)
        except Exception:
            if request_logger:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                request_logger.exception(
                    "outgoing request_id=%s method=%s path=%s status=%s duration_ms=%s",
                    request_id,
                    request.method,
                    request.url.path,
                    500,
                    elapsed_ms,
                )
            raise
        if request_logger:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            request_logger.info(
                "outgoing request_id=%s method=%s path=%s status=%s duration_ms=%s",
                request_id,
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
        return response

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if not api_key:
            return await call_next(request)
        auth = request.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content=ErrorResponse(
                    error={
                        "message": "Missing or invalid Authorization header",
                        "type": "invalid_request_error",
                        "code": "unauthorized",
                    }
                ).model_dump(),
            )
        token = auth[7:].strip()
        if token != api_key:
            return JSONResponse(
                status_code=401,
                content=ErrorResponse(
                    error={
                        "message": "Invalid API key",
                        "type": "invalid_request_error",
                        "code": "unauthorized",
                    }
                ).model_dump(),
            )
        return await call_next(request)

    from sandbox.extensions.web_channel.routes_api import router as api_router
    from sandbox.extensions.web_channel.routes_openai import router as openai_router

    app.include_router(openai_router, tags=["openai"])
    app.include_router(api_router, prefix="/api", tags=["api"])

    return app
