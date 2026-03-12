"""Custom API routes for health, threads, projects, and notifications."""

import time
import uuid

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from core.extensions.persistence.models import ProjectInfo, ThreadInfo
from core.extensions.update_fields import UNSET
from sandbox.extensions.web_channel.models import (
    CreateProjectRequest,
    CreateThreadRequest,
    HealthResponse,
    NotificationsResponse,
    Project,
    Thread,
    ThreadDetailResponse,
    UpdateProjectRequest,
    UpdateThreadRequest,
)

router = APIRouter(include_in_schema=True)


def _get_extension(request: Request):
    return request.app.state.extension


def _thread_model(data: ThreadInfo | dict) -> Thread:
    return Thread.model_validate(data.to_dict() if hasattr(data, "to_dict") else data)


def _project_model(data: ProjectInfo | dict) -> Project:
    return Project.model_validate(data.to_dict() if hasattr(data, "to_dict") else data)


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
        instructions=payload.instructions,
        agent_config=payload.agent_config,
        files=payload.files,
    )
    return JSONResponse(content={"project": _project_model(project).model_dump()})


@router.patch("/projects/{project_id}")
async def patch_project(request: Request, project_id: str) -> JSONResponse:
    """Update project metadata and replace file attachments when provided."""
    ext = _get_extension(request)
    ctx = ext._context
    payload = UpdateProjectRequest.model_validate(await request.json())
    project = await ctx.update_project(
        project_id,
        name=payload.name if "name" in payload.model_fields_set else UNSET,
        instructions=payload.instructions
        if "instructions" in payload.model_fields_set
        else UNSET,
        agent_config=payload.agent_config
        if "agent_config" in payload.model_fields_set
        else UNSET,
        files=payload.files if "files" in payload.model_fields_set else UNSET,
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

