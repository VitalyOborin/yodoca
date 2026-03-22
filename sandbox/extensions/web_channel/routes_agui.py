"""AG-UI protocol routes: POST /agent."""

import asyncio
import uuid
from typing import Any

from ag_ui.core import (
    EventType,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from starlette.responses import JSONResponse

from sandbox.extensions.web_channel.bridge import STREAM_END, RequestBridge
from sandbox.extensions.web_channel.models import AgUIRunRequest, ErrorResponse

router = APIRouter(tags=["ag-ui"])


def _get_extension(request: Request) -> Any:
    return request.app.state.extension


def _extract_last_user_message(messages: list[dict[str, Any]]) -> str:
    """Extract the last user message from AG-UI messages array."""
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


@router.post("/agent", response_model=None)
async def post_agent(request: Request) -> JSONResponse | StreamingResponse:
    """AG-UI protocol endpoint. Streams events over SSE."""
    ext = _get_extension(request)
    bridge: RequestBridge = ext._bridge
    ctx = ext._context
    config = ext._config

    body = await request.json()
    req = AgUIRunRequest.model_validate(body)
    text = _extract_last_user_message(req.messages)
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

    thread_id = request.headers.get("X-Thread-Id") or req.thread_id
    user_id = config.get("default_user_id", "web_user")
    timeout = config.get("request_timeout_seconds", 120)
    accept_header = request.headers.get("accept", "text/event-stream")
    encoder = EventEncoder(accept=accept_header)
    message_id = f"msg_{uuid.uuid4().hex[:24]}"

    payload = {
        "text": text,
        "user_id": user_id,
        "channel_id": ext._channel_id,
    }
    if thread_id:
        payload["thread_id"] = thread_id

    try:
        queue = bridge.create_stream_queue()
        await ctx.emit("user.message", payload)

        async def event_generator():
            try:
                yield encoder.encode(
                    RunStartedEvent(
                        type=EventType.RUN_STARTED,
                        thread_id=req.thread_id,
                        run_id=req.run_id,
                    )
                )

                while True:
                    item = await asyncio.wait_for(queue.get(), timeout=timeout + 5)
                    if item is STREAM_END:
                        break
                    kind, data = item
                    if kind == "start":
                        yield encoder.encode(
                            TextMessageStartEvent(
                                type=EventType.TEXT_MESSAGE_START,
                                message_id=message_id,
                                role="assistant",
                            )
                        )
                    elif kind == "chunk":
                        yield encoder.encode(
                            TextMessageContentEvent(
                                type=EventType.TEXT_MESSAGE_CONTENT,
                                message_id=message_id,
                                delta=data or "",
                            )
                        )
                    elif kind == "status":
                        step_name = data or "tool"
                        yield encoder.encode(
                            StepStartedEvent(
                                type=EventType.STEP_STARTED,
                                step_name=step_name,
                            )
                        )
                        yield encoder.encode(
                            StepFinishedEvent(
                                type=EventType.STEP_FINISHED,
                                step_name=step_name,
                            )
                        )
                    elif kind == "end":
                        yield encoder.encode(
                            TextMessageEndEvent(
                                type=EventType.TEXT_MESSAGE_END,
                                message_id=message_id,
                            )
                        )
                        yield encoder.encode(
                            RunFinishedEvent(
                                type=EventType.RUN_FINISHED,
                                thread_id=req.thread_id,
                                run_id=req.run_id,
                            )
                        )
                        break
            except TimeoutError:
                yield encoder.encode(
                    RunErrorEvent(
                        type=EventType.RUN_ERROR,
                        message="Request timeout",
                        code="timeout",
                    )
                )
            except Exception as e:
                yield encoder.encode(
                    RunErrorEvent(
                        type=EventType.RUN_ERROR,
                        message=str(e),
                        code="internal_error",
                    )
                )
            finally:
                bridge.clear_active()

        return StreamingResponse(
            event_generator(),
            media_type=encoder.get_content_type(),
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
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
