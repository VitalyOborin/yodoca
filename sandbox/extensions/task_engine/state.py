"""TaskState: explicit state for agent loop checkpointing."""

import json
from dataclasses import asdict, dataclass, field


@dataclass
class TaskState:
    """Explicit state for a task. Serialized to JSON and stored in agent_task.checkpoint."""

    goal: str
    step: int = 0
    status: str = "running"
    context: dict = field(default_factory=dict)
    steps_log: list[dict] = field(default_factory=list)
    pending_subtasks: list[str] = field(default_factory=list)
    partial_result: str | None = None
    schema_version: int = 1

    def to_json(self) -> str:
        """Serialize to JSON for checkpoint storage."""
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> "TaskState":
        """Deserialize from checkpoint JSON."""
        if not data or not data.strip():
            raise ValueError("Empty checkpoint data")
        d = json.loads(data)
        return cls(
            goal=d.get("goal", ""),
            step=d.get("step", 0),
            status=d.get("status", "running"),
            context=d.get("context", {}),
            steps_log=d.get("steps_log", []),
            pending_subtasks=d.get("pending_subtasks", []),
            partial_result=d.get("partial_result"),
            schema_version=d.get("schema_version", 1),
        )
