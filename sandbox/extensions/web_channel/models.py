"""Pydantic request/response models for web_channel API (OpenAI format)."""

from typing import Any

from pydantic import BaseModel, model_validator

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
    uptime_seconds: float


class Session(BaseModel):
    """Persisted session metadata."""

    id: str
    project_id: str | None = None
    title: str | None = None
    channel_id: str
    created_at: int
    last_active_at: int
    is_archived: bool = False


class SessionDetailResponse(BaseModel):
    """GET /api/sessions/{id} response."""

    session: Session
    history: list[dict[str, Any]]


class CreateSessionRequest(BaseModel):
    """POST /api/sessions request."""

    id: str | None = None
    project_id: str | None = None
    title: str | None = None


class UpdateSessionRequest(BaseModel):
    """PATCH /api/sessions/{id} request."""

    title: str | None = None
    project_id: str | None = None
    is_archived: bool | None = None


class Project(BaseModel):
    """Persisted project metadata."""

    id: str
    name: str
    instructions: str | None = None
    agent_config: dict[str, Any]
    created_at: int
    updated_at: int
    files: list[str]


class CreateProjectRequest(BaseModel):
    """POST /api/projects request."""

    name: str
    instructions: str | None = None
    agent_config: dict[str, Any] | None = None
    files: list[str] = []


class UpdateProjectRequest(BaseModel):
    """PATCH /api/projects/{id} request."""

    name: str | None = None
    instructions: str | None = None
    agent_config: dict[str, Any] | None = None
    files: list[str] | None = None


class Notification(BaseModel):
    """Single notification item."""

    id: str
    text: str
    created_at: int


class NotificationsResponse(BaseModel):
    """GET /api/notifications response."""

    notifications: list[Notification]


class OperationResult(BaseModel):
    """Generic operation result."""

    success: bool
    message: str | None = None


class ErrorResponse(BaseModel):
    """Error response body."""

    error: dict[str, Any]
