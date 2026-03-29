"""Pydantic request/response models for web_channel API (OpenAI format)."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# --- OpenAI Chat Completions ---


class ChatMessage(BaseModel):
    """OpenAI Chat Completions message."""

    role: str
    content: str | list[dict[str, Any]]


class ChatCompletionsRequest(BaseModel):
    """POST /v1/chat/completions request."""

    model: str = "yodoca"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None


class UsageChat(BaseModel):
    """Token usage for Chat Completions (approximate, may be zero)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionsResponse(BaseModel):
    """POST /v1/chat/completions response (non-streaming)."""

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[dict[str, Any]]
    usage: UsageChat


# --- OpenAI Responses API ---


class ResponsesRequest(BaseModel):
    """POST /v1/responses request. input accepts str or list.

    Also tolerates Chat Completions-style `messages` — converts to `input`
    so clients that hit the wrong endpoint still work.
    """

    model: str = "yodoca"
    input: str | list[dict[str, Any]] | None = None
    messages: list[dict[str, Any]] | None = None
    stream: bool = False

    @model_validator(mode="before")
    @classmethod
    def _coerce_messages_to_input(cls, values: dict[str, Any]) -> dict[str, Any]:
        if not values.get("input") and values.get("messages"):
            values["input"] = values.pop("messages")
        if not values.get("input"):
            raise ValueError("Either 'input' or 'messages' must be provided")
        return values


class UsageResponses(BaseModel):
    """Token usage for Responses API (approximate, may be zero)."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ResponsesResponse(BaseModel):
    """POST /v1/responses response (non-streaming)."""

    id: str
    object: str = "response"
    status: str
    output: list[dict[str, Any]]
    model: str
    usage: UsageResponses


# --- Models list ---


class ModelObject(BaseModel):
    """Single model entry for GET /v1/models."""

    id: str
    object: str = "model"
    created: int
    owned_by: str


class ModelsResponse(BaseModel):
    """GET /v1/models response."""

    object: str = "list"
    data: list[ModelObject]


# --- Custom API ---


class HealthResponse(BaseModel):
    """GET /api/health response."""

    status: str = "ok"
    uptime_seconds: int


class Thread(BaseModel):
    """Persisted thread metadata."""

    id: str
    project_id: str | None = None
    title: str | None = None
    channel_id: str
    created_at: int
    last_active_at: int
    is_archived: bool = False


class ThreadDetailResponse(BaseModel):
    """GET /api/threads/{id} response."""

    thread: Thread
    history: list[dict[str, Any]]


class CreateThreadRequest(BaseModel):
    """POST /api/threads request."""

    id: str | None = None
    project_id: str | None = None
    title: str | None = None


class UpdateThreadRequest(BaseModel):
    """PATCH /api/threads/{id} request."""

    title: str | None = None
    project_id: str | None = None
    is_archived: bool | None = None


class Project(BaseModel):
    """Persisted project metadata."""

    id: str
    name: str
    description: str | None = None
    icon: str | None = None
    instructions: str | None = None
    agent_config: dict[str, Any]
    created_at: int
    updated_at: int
    files: list[str]
    links: list[str]


class CreateProjectRequest(BaseModel):
    """POST /api/projects request."""

    name: str
    description: str | None = None
    icon: str | None = None
    instructions: str | None = None
    agent_config: dict[str, Any] | None = None
    files: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)


class UpdateProjectRequest(BaseModel):
    """PATCH /api/projects/{id} request."""

    name: str | None = None
    description: str | None = None
    icon: str | None = None
    instructions: str | None = None
    agent_config: dict[str, Any] | None = None
    files: list[str] | None = None
    links: list[str] | None = None


class Notification(BaseModel):
    """Single notification item."""

    id: str
    text: str
    created_at: int


class NotificationsResponse(BaseModel):
    """GET /api/notifications response."""

    notifications: list[Notification]


class CompanionPresenceResponse(BaseModel):
    """GET /api/companion/presence response."""

    success: bool
    status: str = "ok"
    health: bool | None = None
    phase: str | None = None
    presence_state: str | None = None
    mood: float | None = None
    time_in_phase_seconds: int | None = None
    last_tick_at: str | None = None
    lifecycle_phase: str | None = None
    estimated_availability: float | None = None
    llm_degraded: bool | None = None
    error: str | None = None


class InboxItem(BaseModel):
    """Single inbox item (current snapshot)."""

    id: int
    source_type: str
    source_account: str
    entity_type: str
    external_id: str
    title: str = ""
    occurred_at: float
    ingested_at: float
    status: str = "active"
    is_read: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)


class InboxListResponse(BaseModel):
    """GET /api/inbox response."""

    items: list[InboxItem] = Field(default_factory=list)
    total: int = 0
    unread_count: int = 0
    limit: int = 50
    offset: int = 0


class OperationResult(BaseModel):
    """Generic operation result."""

    success: bool
    message: str | None = None


# --- Schedules API ---


class ScheduleItem(BaseModel):
    """Single schedule entry (one-shot or recurring)."""

    id: int
    type: Literal["one_shot", "recurring"]
    topic: str
    message: str | None = None
    channel_id: str | None = None
    payload: dict[str, Any]
    fires_at_iso: str
    status: str
    cron_expr: str | None = None
    every_seconds: float | None = None
    until_iso: str | None = None
    created_at: int


class ScheduleListResponse(BaseModel):
    """GET /api/schedules response."""

    schedules: list[ScheduleItem]
    count: int


class CreateOnceScheduleRequest(BaseModel):
    """POST /api/schedules/once request."""

    topic: str
    message: str
    channel_id: str | None = None
    payload_extra: dict[str, Any] | None = None
    delay_seconds: int | None = Field(default=None, ge=1)
    at_iso: str | None = None

    @model_validator(mode="after")
    def _validate_time_selector(self) -> "CreateOnceScheduleRequest":
        if (self.delay_seconds is None) == (self.at_iso is None):
            raise ValueError("provide exactly one of delay_seconds or at_iso")
        return self


class ScheduleOnceResponse(BaseModel):
    """POST /api/schedules/once response."""

    success: bool
    schedule_id: int
    topic: str
    fires_in_seconds: int
    status: Literal["scheduled"]
    error: str | None = None


class CreateRecurringScheduleRequest(BaseModel):
    """POST /api/schedules/recurring request."""

    topic: str
    message: str
    channel_id: str | None = None
    payload_extra: dict[str, Any] | None = None
    cron: str | None = None
    every_seconds: float | None = Field(default=None, ge=1)
    until_iso: str | None = None

    @model_validator(mode="after")
    def _validate_schedule_selector(self) -> "CreateRecurringScheduleRequest":
        has_cron = bool(self.cron and self.cron.strip())
        has_interval = self.every_seconds is not None
        if has_cron == has_interval:
            raise ValueError("provide exactly one of cron or every_seconds")
        return self


class ScheduleRecurringResponse(BaseModel):
    """POST /api/schedules/recurring response."""

    success: bool
    schedule_id: int
    next_fire_iso: str
    status: Literal["created"]
    error: str | None = None


class UpdateScheduleRequest(BaseModel):
    """PATCH /api/schedules/{type}/{id} request."""

    cron: str | None = None
    every_seconds: float | None = Field(default=None, ge=1)
    until_iso: str | None = None
    status: Literal["active", "paused"] | None = None

    @model_validator(mode="after")
    def _validate_selector(self) -> "UpdateScheduleRequest":
        has_cron = self.cron is not None and bool(self.cron.strip())
        has_interval = self.every_seconds is not None
        if has_cron and has_interval:
            raise ValueError("provide only one of cron or every_seconds")
        return self


class UpdateScheduleResponse(BaseModel):
    """PATCH /api/schedules/{type}/{id} response."""

    success: bool
    schedule_id: int
    next_fire_iso: str
    message: str | None = None
    error: str | None = None


# --- Tasks API ---


class TaskItem(BaseModel):
    """Single task status/details item."""

    task_id: str
    status: str
    agent_id: str
    goal: str
    step: int
    max_steps: int
    attempt_no: int
    partial_result: str | None = None
    error: str | None = None
    chain_id: str | None = None
    chain_order: int | None = None
    created_at: int
    updated_at: int


class TaskListResponse(BaseModel):
    """GET /api/tasks response."""

    tasks: list[TaskItem]
    total: int


class CancelTaskRequest(BaseModel):
    """POST /api/tasks/{task_id}/cancel request."""

    reason: str = ""


class CancelTaskResponse(BaseModel):
    """POST /api/tasks/{task_id}/cancel response."""

    task_id: str
    status: str
    message: str


# --- AG-UI (Agent-User Interaction Protocol) ---


class AgUIRunRequest(BaseModel):
    """POST /agent request (AG-UI RunAgentInput shape).

    Accepts camelCase over the wire for AG-UI client compatibility.
    """

    thread_id: str = Field(..., alias="threadId")
    run_id: str = Field(..., alias="runId")
    parent_run_id: str | None = Field(None, alias="parentRunId")
    messages: list[dict[str, Any]] = Field(default_factory=list, alias="messages")
    tools: list[dict[str, Any]] = Field(default_factory=list, alias="tools")
    context: list[dict[str, Any]] = Field(default_factory=list, alias="context")
    state: Any = Field(default=None, alias="state")
    forwarded_props: Any = Field(default=None, alias="forwardedProps")

    model_config = {"populate_by_name": True}


class ErrorResponse(BaseModel):
    """Error response body."""

    error: dict[str, Any]
