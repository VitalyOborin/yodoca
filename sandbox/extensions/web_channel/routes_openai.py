"""OpenAI-compatible routes: /v1/models, /v1/chat/completions, /v1/responses."""

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from starlette.responses import JSONResponse

from sandbox.extensions.web_channel.bridge import STREAM_END, RequestBridge
from sandbox.extensions.web_channel.models import (
    ChatCompletionsRequest,
    ChatCompletionsResponse,
    ErrorResponse,
    ModelObject,
    ModelsResponse,
    ResponsesRequest,
    ResponsesResponse,
    UsageChat,
    UsageResponses,
)
from sandbox.extensions.web_channel.streaming import (
    format_chat_chunk,
    format_chat_done,
    format_responses_event,
    generate_chat_id,
    generate_msg_id,
    generate_response_id,
)

router = APIRouter(prefix="/v1", include_in_schema=True)


def _get_extension(request: Request) -> Any:
    return request.app.state.extension


def _extract_last_user_message(messages: list[dict[str, Any]]) -> str:
    """Extract the last user message from messages array."""
    for m in reversed(messages):
        role = m.get("role", "")
        if role == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(p.get("text", ""))
                    elif isinstance(p, dict) and "text" in p:
                        parts.append(str(p["text"]))
                return " ".join(parts).strip() if parts else ""
            return str(content).strip()
    return ""


def _extract_from_input(input_val: str | list[dict[str, Any]]) -> str:
    """Extract last user message from Responses API input (str or list)."""
    if isinstance(input_val, str):
        return input_val.strip()
    if isinstance(input_val, list):
        return _extract_last_user_message(input_val)
    return ""


@router.get("/models")
async def get_models(request: Request) -> ModelsResponse:
    """List available models (virtual model from config)."""
    ext = _get_extension(request)
    model_name = ext._config.get("model_name", "yodoca")
    return ModelsResponse(
        object="list",
        data=[
            ModelObject(
                id=model_name,
                object="model",
                created=int(time.time()),
                owned_by="yodoca",
            )
        ],
    )


@router.post("/chat/completions", response_model=None)
async def post_chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    """Chat Completions endpoint (OpenAI format)."""
    ext = _get_extension(request)
    bridge: RequestBridge = ext._bridge
    ctx = ext._context
    config = ext._config

    body = await request.json()
    req = ChatCompletionsRequest.model_validate(body)
    text = _extract_last_user_message([m.model_dump() for m in req.messages])
    if not text:
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error={
                    "message": "No user message found in messages",
                    "type": "invalid_request_error",
                    "code": "invalid_messages",
                }
            ).model_dump(),
        )

    if not await bridge.acquire():
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "5"},
            content=ErrorResponse(
                error={
                    "message": "Another request is being processed. Retry shortly.",
                    "type": "server_error",
                    "code": "busy",
                }
            ).model_dump(),
        )

    session_id = request.headers.get("X-Session-Id")
    user_id = config.get("default_user_id", "web_user")
    timeout = config.get("request_timeout_seconds", 120)
    model_name = config.get("model_name", "yodoca")
    chat_id = generate_chat_id()
    created = int(time.time())

    payload = {
        "text": text,
        "user_id": user_id,
        "channel_id": ext._channel_id,
    }
    if session_id:
        payload["session_id"] = session_id

    try:
        if req.stream:
            queue = bridge.create_stream_queue()
            await ctx.emit("user.message", payload)

            async def event_generator():
                try:
                    while True:
                        item = await asyncio.wait_for(queue.get(), timeout=timeout + 5)
                        if item is STREAM_END:
                            yield format_chat_done()
                            break
                        kind, data = item
                        if kind == "start":
                            yield format_chat_chunk(
                                chat_id, model_name, "", None, created
                            )
                        elif kind == "chunk":
                            yield format_chat_chunk(
                                chat_id, model_name, data or "", None, created
                            )
                        elif kind == "end":
                            yield format_chat_chunk(
                                chat_id, model_name, "", "stop", created
                            )
                            yield format_chat_done()
                            break
                finally:
                    bridge.clear_active()

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            future = bridge.create_future()
            await ctx.emit("user.message", payload)
            response_text = await asyncio.wait_for(future, timeout=timeout)
            bridge.clear_active()

            return JSONResponse(
                content=ChatCompletionsResponse(
                    id=chat_id,
                    object="chat.completion",
                    created=created,
                    model=model_name,
                    choices=[
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": response_text,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    usage=UsageChat(),
                ).model_dump()
            )
    except TimeoutError:
        bridge.release()
        bridge.clear_active()
        return JSONResponse(
            status_code=504,
            content=ErrorResponse(
                error={
                    "message": "Request timeout",
                    "type": "server_error",
                    "code": "timeout",
                }
            ).model_dump(),
        )
    except Exception:
        bridge.release()
        bridge.clear_active()
        raise


@router.post("/responses", response_model=None)
async def post_responses(request: Request) -> JSONResponse | StreamingResponse:
    """OpenAI Responses API endpoint."""
    ext = _get_extension(request)
    bridge: RequestBridge = ext._bridge
    ctx = ext._context
    config = ext._config

    body = await request.json()
    req = ResponsesRequest.model_validate(body)
    text = _extract_from_input(req.input)
    if not text:
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error={
                    "message": "No user input found",
                    "type": "invalid_request_error",
                    "code": "invalid_input",
                }
            ).model_dump(),
        )

    if not await bridge.acquire():
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "5"},
            content=ErrorResponse(
                error={
                    "message": "Another request is being processed. Retry shortly.",
                    "type": "server_error",
                    "code": "busy",
                }
            ).model_dump(),
        )

    session_id = request.headers.get("X-Session-Id")
    user_id = config.get("default_user_id", "web_user")
    timeout = config.get("request_timeout_seconds", 120)
    model_name = config.get("model_name", "yodoca")
    resp_id = generate_response_id()
    msg_id = generate_msg_id()
    created = int(time.time())

    payload = {
        "text": text,
        "user_id": user_id,
        "channel_id": ext._channel_id,
    }
    if session_id:
        payload["session_id"] = session_id

    try:
        if req.stream:
            queue = bridge.create_stream_queue()
            await ctx.emit("user.message", payload)

            async def event_generator():
                try:
                    while True:
                        item = await asyncio.wait_for(queue.get(), timeout=timeout + 5)
                        if item is STREAM_END:
                            break
                        kind, data = item
                        if kind == "start":
                            yield format_responses_event(
                                "response.created",
                                {
                                    "id": resp_id,
                                    "object": "response",
                                    "status": "in_progress",
                                    "model": model_name,
                                    "created": created,
                                },
                            )
                            yield format_responses_event(
                                "response.output_item.added",
                                {
                                    "item": {
                                        "id": msg_id,
                                        "type": "message",
                                        "role": "assistant",
                                        "content": [],
                                    }
                                },
                            )
                            yield format_responses_event(
                                "response.content_part.added",
                                {
                                    "item_id": msg_id,
                                    "content_index": 0,
                                    "part": {"type": "output_text", "text": ""},
                                },
                            )
                        elif kind == "chunk":
                            yield format_responses_event(
                                "response.output_text.delta",
                                {
                                    "item_id": msg_id,
                                    "content_index": 0,
                                    "delta": data or "",
                                },
                            )
                        elif kind == "status":
                            yield format_responses_event(
                                "response.output_item.added",
                                {
                                    "item": {
                                        "type": "status_update",
                                        "text": data or "",
                                    }
                                },
                            )
                        elif kind == "end":
                            yield format_responses_event(
                                "response.output_text.done",
                                {
                                    "item_id": msg_id,
                                    "content_index": 0,
                                    "text": data or "",
                                },
                            )
                            yield format_responses_event(
                                "response.completed",
                                {
                                    "id": resp_id,
                                    "object": "response",
                                    "status": "completed",
                                    "model": model_name,
                                    "usage": {
                                        "input_tokens": 0,
                                        "output_tokens": 0,
                                        "total_tokens": 0,
                                    },
                                },
                            )
                            break
                finally:
                    bridge.clear_active()

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            future = bridge.create_future()
            await ctx.emit("user.message", payload)
            response_text = await asyncio.wait_for(future, timeout=timeout)
            bridge.clear_active()

            return JSONResponse(
                content=ResponsesResponse(
                    id=resp_id,
                    object="response",
                    status="completed",
                    output=[
                        {
                            "id": msg_id,
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": response_text}
                            ],
                        }
                    ],
                    model=model_name,
                    usage=UsageResponses(),
                ).model_dump()
            )
    except TimeoutError:
        bridge.release()
        bridge.clear_active()
        return JSONResponse(
            status_code=504,
            content=ErrorResponse(
                error={
                    "message": "Request timeout",
                    "type": "server_error",
                    "code": "timeout",
                }
            ).model_dump(),
        )
    except Exception:
        bridge.release()
        bridge.clear_active()
        raise
