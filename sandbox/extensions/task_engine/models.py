"""Task Engine data models: Pydantic tool results and internal dataclasses."""

from dataclasses import dataclass

from pydantic import BaseModel, Field


# --- Tool result models (returned by Orchestrator tools) ---


class SubmitTaskResult(BaseModel):
    """Result of submit_task tool."""

    task_id: str
    status: str
    message: str


class TaskStatusResult(BaseModel):
    """Result of get_task_status tool."""

    task_id: str
    status: str
    agent_id: str
    goal: str
    step: int
    max_steps: int
    attempt_no: int
    partial_result: str | None = None
    error: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0


class ActiveTasksResult(BaseModel):
    """Result of list_active_tasks tool."""

    tasks: list[TaskStatusResult] = Field(default_factory=list)
    total: int = 0


class CancelTaskResult(BaseModel):
    """Result of cancel_task tool."""

    task_id: str
    status: str
    message: str


# --- Internal dataclasses (DB row mapping) ---


@dataclass
class TaskRecord:
    """Internal representation of an agent_task row."""

    task_id: str
    parent_id: str | None
    run_id: str
    agent_id: str
    status: str
    priority: int
    payload: dict
    result: dict | None
    checkpoint: str | None
    error: str | None
    attempt_no: int
    schedule_at: float | None
    leased_by: str | None
    lease_exp: float | None
    created_at: float
    updated_at: float


@dataclass
class StepRecord:
    """Internal representation of a task_step row."""

    step_id: str
    task_id: str
    step_no: int
    step_type: str
    status: str
    idempotency_key: str | None = None
    input_ref: str | None = None
    output_ref: str | None = None
    tokens_used: int | None = None
    duration_ms: int | None = None
    error_code: str | None = None
    created_at: float = 0.0
