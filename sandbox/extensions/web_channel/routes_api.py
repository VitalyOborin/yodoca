"""Custom API routes for health, threads, projects, and notifications."""

import json
import time
import uuid
from datetime import UTC, datetime

from croniter import croniter
from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from core.extensions.persistence.models import ProjectInfo, ThreadInfo
from core.extensions.update_fields import UNSET
from sandbox.extensions.web_channel.models import (
    CreateOnceScheduleRequest,
    CreateProjectRequest,
    CreateRecurringScheduleRequest,
    CreateThreadRequest,
    HealthResponse,
    NotificationsResponse,
    OperationResult,
    Project,
    ScheduleItem,
    ScheduleListResponse,
    ScheduleOnceResponse,
    ScheduleRecurringResponse,
    Thread,
    ThreadDetailResponse,
    UpdateProjectRequest,
    UpdateScheduleRequest,
    UpdateScheduleResponse,
    UpdateThreadRequest,
)

router = APIRouter(include_in_schema=True)


def _get_extension(request: Request):
    return request.app.state.extension


def _thread_model(data: ThreadInfo | dict) -> Thread:
    return Thread.model_validate(data.to_dict() if hasattr(data, "to_dict") else data)


def _project_model(data: ProjectInfo | dict) -> Project:
    return Project.model_validate(data.to_dict() if hasattr(data, "to_dict") else data)


def _scheduler_unavailable_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "message": "Scheduler extension is not loaded or not initialized",
                "type": "service_unavailable",
                "code": "scheduler_unavailable",
            }
        },
    )


def _parse_iso_to_timestamp(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _to_utc_iso(timestamp: float) -> str:
    return (
        datetime.fromtimestamp(timestamp, UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _decode_payload(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except (TypeError, ValueError):
            return {"raw": raw}
    return {"value": raw}


def _payload_message(topic: str, payload: dict) -> str | None:
    if topic == "system.user.notify":
        value = payload.get("text")
        return value if isinstance(value, str) else None
    if topic in ("system.agent.task", "system.agent.background"):
        value = payload.get("prompt")
        return value if isinstance(value, str) else None
    for key in ("message", "text", "prompt"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def _build_event_payload(
    topic: str,
    message: str,
    channel_id: str | None,
    payload_extra: dict | None,
) -> dict:
    if topic == "system.user.notify":
        payload: dict = {"text": message}
    elif topic in ("system.agent.task", "system.agent.background"):
        payload = {"prompt": message}
    elif payload_extra:
        payload = dict(payload_extra)
        if (
            "message" not in payload
            and "text" not in payload
            and "prompt" not in payload
        ):
            payload["message"] = message
    else:
        payload = {"message": message}
    if channel_id:
        payload["channel_id"] = channel_id
    return payload


def _schedule_item_model(row: dict) -> ScheduleItem:
    payload = _decode_payload(row.get("payload"))
    fire_ts = row.get("fire_at_or_next")
    until_ts = row.get("until_at")
    return ScheduleItem(
        id=int(row["id"]),
        type=row["type"],
        topic=row["topic"],
        message=_payload_message(row["topic"], payload),
        channel_id=payload.get("channel_id")
        if isinstance(payload.get("channel_id"), str)
        else None,
        payload=payload,
        fires_at_iso=_to_utc_iso(float(fire_ts)),
        status=row["status"],
        cron_expr=row.get("cron_expr"),
        every_seconds=row.get("every_sec"),
        until_iso=_to_utc_iso(float(until_ts))
        if until_ts is not None
        else None,
        created_at=int(row.get("created_at", 0)),
    )


def _filter_schedule_rows(rows: list[dict], status: str | None) -> list[dict]:
    if not status:
        return rows
    if status == "active":
        return [
            row
            for row in rows
            if (row.get("type") == "one_shot" and row.get("status") == "scheduled")
            or (row.get("type") == "recurring" and row.get("status") == "active")
        ]
    return [row for row in rows if row.get("status") == status]


def _get_scheduler_store(request: Request):
    ext = _get_extension(request)
    scheduler = ext.get_scheduler()
    if not scheduler:
        return None
    return getattr(scheduler, "_store", None)


@router.get("/health")
async def get_health(request: Request) -> HealthResponse:
    """Health check with uptime."""
    start = getattr(request.app.state, "start_monotonic", None)
    if start is None:
        start = time.monotonic()
        request.app.state.start_monotonic = start
    uptime = time.monotonic() - start
    return HealthResponse(status="ok", uptime_seconds=int(uptime))


@router.get("/threads")
async def get_threads(request: Request) -> JSONResponse:
    """List thread metadata from the persistent repository."""
    ext = _get_extension(request)
    ctx = ext._context
    threads = await ctx.list_threads()
    data = {"threads": [_thread_model(thread).model_dump() for thread in threads]}
    return JSONResponse(content=data)


@router.post("/threads")
async def create_thread(request: Request) -> JSONResponse:
    """Create a persistent thread row before any messages are sent."""
    ext = _get_extension(request)
    ctx = ext._context
    payload = CreateThreadRequest.model_validate(await request.json())
    try:
        thread = await ctx.create_thread(
            thread_id=payload.id or f"thread_{uuid.uuid4().hex}",
            channel_id=ext._channel_id,
            project_id=payload.project_id,
            title=payload.title,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": str(exc),
                    "type": "invalid_request_error",
                    "code": "invalid_project_id",
                }
            },
        )
    return JSONResponse(content={"thread": _thread_model(thread).model_dump()})


@router.get("/threads/{thread_id}")
async def get_thread(request: Request, thread_id: str) -> JSONResponse:
    """Return one thread metadata record and stored history."""
    ext = _get_extension(request)
    ctx = ext._context
    thread = await ctx.get_thread(thread_id, include_archived=True)
    history = await ctx.get_thread_history(thread_id)
    if thread is None or history is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": f"Thread {thread_id} not found",
                    "type": "not_found",
                    "code": "thread_not_found",
                }
            },
        )
    response = ThreadDetailResponse(
        thread=_thread_model(thread),
        history=history,
    )
    return JSONResponse(content=response.model_dump())


@router.patch("/threads/{thread_id}")
async def patch_thread(request: Request, thread_id: str) -> JSONResponse:
    """Update title, project binding, or archive state for a thread."""
    ext = _get_extension(request)
    ctx = ext._context
    payload = UpdateThreadRequest.model_validate(await request.json())
    try:
        thread = await ctx.update_thread(
            thread_id,
            title=payload.title if "title" in payload.model_fields_set else UNSET,
            project_id=payload.project_id
            if "project_id" in payload.model_fields_set
            else UNSET,
            is_archived=payload.is_archived
            if "is_archived" in payload.model_fields_set
            else UNSET,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": str(exc),
                    "type": "invalid_request_error",
                    "code": "invalid_project_id",
                }
            },
        )
    if thread is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": f"Thread {thread_id} not found",
                    "type": "not_found",
                    "code": "thread_not_found",
                }
            },
        )
    return JSONResponse(content={"thread": _thread_model(thread).model_dump()})


@router.delete("/threads/{thread_id}")
async def delete_thread(request: Request, thread_id: str) -> JSONResponse:
    """Soft-archive a thread without deleting its history."""
    ext = _get_extension(request)
    ctx = ext._context
    archived = await ctx.archive_thread(thread_id)
    if not archived:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": f"Thread {thread_id} not found",
                    "type": "not_found",
                    "code": "thread_not_found",
                }
            },
        )
    return JSONResponse(
        content={
            "success": True,
            "message": (
                "Thread archived. Restore it with PATCH /api/threads/{id} "
                "and is_archived=false."
            ),
        }
    )


@router.get("/projects")
async def get_projects(request: Request) -> JSONResponse:
    """List project metadata without embedded threads."""
    ext = _get_extension(request)
    ctx = ext._context
    projects = await ctx.list_projects()
    data = {"projects": [_project_model(project).model_dump() for project in projects]}
    return JSONResponse(content=data)


@router.get("/projects/{project_id}")
async def get_project(request: Request, project_id: str) -> JSONResponse:
    """Return one project metadata record."""
    ext = _get_extension(request)
    ctx = ext._context
    project = await ctx.get_project(project_id)
    if project is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": f"Project {project_id} not found",
                    "type": "not_found",
                    "code": "project_not_found",
                }
            },
        )
    return JSONResponse(content={"project": _project_model(project).model_dump()})


@router.post("/projects")
async def create_project(request: Request) -> JSONResponse:
    """Create a project record and its file-path attachments."""
    ext = _get_extension(request)
    ctx = ext._context
    payload = CreateProjectRequest.model_validate(await request.json())
    project = await ctx.create_project(
        name=payload.name,
        description=payload.description,
        icon=payload.icon,
        instructions=payload.instructions,
        agent_config=payload.agent_config,
        files=payload.files,
        links=payload.links,
    )
    return JSONResponse(content={"project": _project_model(project).model_dump()})


@router.patch("/projects/{project_id}")
async def patch_project(request: Request, project_id: str) -> JSONResponse:
    """Update project metadata and replace file/link attachments when provided."""
    ext = _get_extension(request)
    ctx = ext._context
    payload = UpdateProjectRequest.model_validate(await request.json())
    project = await ctx.update_project(
        project_id,
        name=payload.name if "name" in payload.model_fields_set else UNSET,
        description=payload.description
        if "description" in payload.model_fields_set
        else UNSET,
        icon=payload.icon if "icon" in payload.model_fields_set else UNSET,
        instructions=payload.instructions
        if "instructions" in payload.model_fields_set
        else UNSET,
        agent_config=payload.agent_config
        if "agent_config" in payload.model_fields_set
        else UNSET,
        files=payload.files if "files" in payload.model_fields_set else UNSET,
        links=payload.links if "links" in payload.model_fields_set else UNSET,
    )
    if project is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": f"Project {project_id} not found",
                    "type": "not_found",
                    "code": "project_not_found",
                }
            },
        )
    return JSONResponse(content={"project": _project_model(project).model_dump()})


@router.delete("/projects/{project_id}")
async def delete_project(request: Request, project_id: str) -> JSONResponse:
    """Delete a project. Bound threads are unlinked via foreign-key rules."""
    ext = _get_extension(request)
    ctx = ext._context
    deleted = await ctx.delete_project(project_id)
    if not deleted:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": f"Project {project_id} not found",
                    "type": "not_found",
                    "code": "project_not_found",
                }
            },
        )
    return JSONResponse(
        content={
            "success": True,
            "message": "Project deleted and bound threads were unlinked.",
        }
    )


@router.get("/schedules")
async def get_schedules(request: Request, status: str | None = None) -> JSONResponse:
    """List one-shot and recurring schedules."""
    if status and status not in {"scheduled", "fired", "active", "paused", "cancelled"}:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "Invalid status filter",
                    "type": "invalid_request_error",
                    "code": "invalid_status",
                }
            },
        )
    store = _get_scheduler_store(request)
    if not store:
        return _scheduler_unavailable_response()

    rows = await store.list_all()
    filtered_rows = _filter_schedule_rows(rows, status)
    schedules = [_schedule_item_model(row) for row in filtered_rows]
    response = ScheduleListResponse(
        schedules=schedules,
        count=len(schedules),
    )
    return JSONResponse(content=response.model_dump())


@router.post("/schedules/once")
async def create_once_schedule(request: Request) -> JSONResponse:
    """Create one-shot schedule."""
    store = _get_scheduler_store(request)
    if not store:
        return _scheduler_unavailable_response()

    try:
        payload = CreateOnceScheduleRequest.model_validate(await request.json())
    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": str(exc),
                    "type": "invalid_request_error",
                    "code": "invalid_schedule_payload",
                }
            },
        )

    now = time.time()
    fire_at: float
    if payload.delay_seconds is not None:
        fire_at = now + payload.delay_seconds
    else:
        fire_at = _parse_iso_to_timestamp(payload.at_iso)
        if fire_at is None:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": "Invalid at_iso format. Use ISO 8601.",
                        "type": "invalid_request_error",
                        "code": "invalid_datetime",
                    }
                },
            )
        if fire_at <= now:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": "at_iso must be in the future.",
                        "type": "invalid_request_error",
                        "code": "invalid_datetime",
                    }
                },
            )

    event_payload = _build_event_payload(
        topic=payload.topic,
        message=payload.message,
        channel_id=payload.channel_id,
        payload_extra=payload.payload_extra,
    )
    schedule_id = await store.insert_one_shot(
        payload.topic,
        json.dumps(event_payload, ensure_ascii=False),
        fire_at,
    )
    response = ScheduleOnceResponse(
        success=True,
        schedule_id=schedule_id,
        topic=payload.topic,
        fires_in_seconds=max(int(fire_at - now), 0),
        status="scheduled",
    )
    return JSONResponse(status_code=201, content=response.model_dump())


@router.post("/schedules/recurring")
async def create_recurring_schedule(request: Request) -> JSONResponse:
    """Create recurring schedule."""
    store = _get_scheduler_store(request)
    if not store:
        return _scheduler_unavailable_response()

    try:
        payload = CreateRecurringScheduleRequest.model_validate(await request.json())
    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": str(exc),
                    "type": "invalid_request_error",
                    "code": "invalid_schedule_payload",
                }
            },
        )

    now = time.time()
    if payload.cron:
        try:
            next_fire = croniter(payload.cron.strip(), now).get_next(float)
        except (ValueError, KeyError) as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": f"Invalid cron expression: {exc}",
                        "type": "invalid_request_error",
                        "code": "invalid_cron",
                    }
                },
            )
    else:
        next_fire = now + (payload.every_seconds or 0)

    until_at = _parse_iso_to_timestamp(payload.until_iso) if payload.until_iso else None
    if payload.until_iso and until_at is None:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "Invalid until_iso format. Use ISO 8601.",
                    "type": "invalid_request_error",
                    "code": "invalid_datetime",
                }
            },
        )
    if until_at is not None and until_at <= now:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "until_iso must be in the future.",
                    "type": "invalid_request_error",
                    "code": "invalid_datetime",
                }
            },
        )

    event_payload = _build_event_payload(
        topic=payload.topic,
        message=payload.message,
        channel_id=payload.channel_id,
        payload_extra=payload.payload_extra,
    )
    schedule_id = await store.insert_recurring(
        payload.topic,
        json.dumps(event_payload, ensure_ascii=False),
        payload.cron.strip() if payload.cron else None,
        payload.every_seconds,
        until_at,
        next_fire,
    )
    response = ScheduleRecurringResponse(
        success=True,
        schedule_id=schedule_id,
        next_fire_iso=_to_utc_iso(next_fire),
        status="created",
    )
    return JSONResponse(status_code=201, content=response.model_dump())


@router.delete("/schedules/{type}/{id}")
async def delete_schedule(request: Request, type: str, id: int) -> JSONResponse:
    """Cancel one-shot or recurring schedule."""
    if type not in {"one_shot", "recurring"}:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "Invalid schedule type",
                    "type": "invalid_request_error",
                    "code": "invalid_schedule_type",
                }
            },
        )
    store = _get_scheduler_store(request)
    if not store:
        return _scheduler_unavailable_response()

    rows = await store.list_all()
    row = next(
        (
            item
            for item in rows
            if item.get("type") == type and item.get("id") == id
        ),
        None,
    )
    if row is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": f"Schedule {type}/{id} not found",
                    "type": "not_found",
                    "code": "schedule_not_found",
                }
            },
        )

    if type == "one_shot":
        if row.get("status") != "scheduled":
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "message": "Cannot cancel one-shot schedule in current status",
                        "type": "conflict",
                        "code": "schedule_conflict",
                    }
                },
            )
        ok = await store.cancel_one_shot(id)
        if not ok:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "message": "Cannot cancel one-shot schedule in current status",
                        "type": "conflict",
                        "code": "schedule_conflict",
                    }
                },
            )
    else:
        await store.cancel_recurring(id)

    result = OperationResult(success=True, message=f"Schedule {type}/{id} cancelled.")
    return JSONResponse(content=result.model_dump())


@router.patch("/schedules/{type}/{id}")
async def patch_schedule(request: Request, type: str, id: int) -> JSONResponse:
    """Update recurring schedule fields or status."""
    if type not in {"one_shot", "recurring"}:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "Invalid schedule type",
                    "type": "invalid_request_error",
                    "code": "invalid_schedule_type",
                }
            },
        )
    if type == "one_shot":
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "message": "PATCH is supported only for recurring schedules",
                    "type": "invalid_request_error",
                    "code": "one_shot_update_not_supported",
                }
            },
        )

    store = _get_scheduler_store(request)
    if not store:
        return _scheduler_unavailable_response()

    raw_payload = await request.json()
    try:
        payload = UpdateScheduleRequest.model_validate(raw_payload)
    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": str(exc),
                    "type": "invalid_request_error",
                    "code": "invalid_schedule_payload",
                }
            },
        )

    now = time.time()
    if payload.cron:
        try:
            croniter(payload.cron.strip(), now).get_next(float)
        except (ValueError, KeyError) as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": f"Invalid cron expression: {exc}",
                        "type": "invalid_request_error",
                        "code": "invalid_cron",
                    }
                },
            )

    until_at = None
    if "until_iso" in payload.model_fields_set and payload.until_iso is not None:
        until_at = _parse_iso_to_timestamp(payload.until_iso)
        if until_at is None:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": "Invalid until_iso format. Use ISO 8601.",
                        "type": "invalid_request_error",
                        "code": "invalid_datetime",
                    }
                },
            )
        if until_at <= now:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": "until_iso must be in the future.",
                        "type": "invalid_request_error",
                        "code": "invalid_datetime",
                    }
                },
            )

    next_fire = await store.update_recurring(
        id,
        cron_expr=payload.cron.strip() if payload.cron else None,
        every_sec=payload.every_seconds,
        until_at=until_at,
        status=payload.status,
        set_until="until_iso" in payload.model_fields_set,
    )
    if next_fire is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": f"Recurring schedule {id} not found",
                    "type": "not_found",
                    "code": "schedule_not_found",
                }
            },
        )
    response = UpdateScheduleResponse(
        success=True,
        schedule_id=id,
        next_fire_iso=_to_utc_iso(next_fire),
        message=f"Schedule #{id} updated.",
    )
    return JSONResponse(content=response.model_dump())


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

