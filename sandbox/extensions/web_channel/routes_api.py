"""Custom API routes for health, conversations, and notifications."""

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from sandbox.extensions.web_channel.models import (
    Conversation,
    HealthResponse,
    NotificationsResponse,
    OperationResult,
)

router = APIRouter(include_in_schema=True)

_start_time: float | None = None


def _get_extension(request: Request):
    return request.app.state.extension


@router.get("/health")
async def get_health(request: Request) -> HealthResponse:
    """Health check with uptime."""
    import time

    global _start_time
    if _start_time is None:
        _start_time = time.monotonic()
    uptime = time.monotonic() - _start_time
    return HealthResponse(status="ok", uptime_seconds=uptime)


@router.get("/conversations")
async def get_conversations(request: Request) -> JSONResponse:
    """List conversation sessions from session pool."""
    ext = _get_extension(request)
    ctx = ext._context
    summaries = await ctx.list_session_summaries()
    conversations = [
        Conversation(
            id=summary["id"],
            title=None,
            updated_at=int(summary["updated_at"]),
        )
        for summary in summaries
    ]
    data = {"conversations": [c.model_dump() for c in conversations]}
    return JSONResponse(content=data)


@router.delete("/conversations/{session_id}")
async def delete_conversation(
    request: Request, session_id: str
) -> JSONResponse:
    """Delete a conversation session from the pool."""
    ext = _get_extension(request)
    ctx = ext._context
    deleted = ctx.delete_session(session_id)
    if not deleted:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": f"Session {session_id} not found",
                    "type": "not_found",
                    "code": "session_not_found",
                }
            },
        )
    return JSONResponse(
        content=OperationResult(success=True, message="Session deleted").model_dump()
    )


@router.get("/conversations/{session_id}")
async def get_conversation(
    request: Request, session_id: str
) -> JSONResponse:
    """Return one conversation session metadata and stored history."""
    ext = _get_extension(request)
    ctx = ext._context
    history = await ctx.get_session_history(session_id)
    if history is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": f"Session {session_id} not found",
                    "type": "not_found",
                    "code": "session_not_found",
                }
            },
        )
    updated_at = await ctx.get_session_updated_at(session_id)
    conversation = Conversation(
        id=session_id,
        title=None,
        updated_at=updated_at or 0,
    )
    return JSONResponse(
        content={
            "conversation": conversation.model_dump(),
            "history": history,
        }
    )


@router.get("/notifications")
async def get_notifications(request: Request) -> NotificationsResponse:
    """Long-poll for proactive notifications."""
    ext = _get_extension(request)
    bridge = ext._bridge
    timeout = min(
        int(request.query_params.get("timeout", 30)),
        60,
    )
    if timeout < 1:
        timeout = 30
    notifications = await bridge.wait_notification(timeout=float(timeout))
    return NotificationsResponse(notifications=notifications)
