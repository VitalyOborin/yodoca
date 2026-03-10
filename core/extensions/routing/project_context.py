"""Built-in ContextProvider for project-level instructions."""

from core.extensions.contract import TurnContext
from core.extensions.persistence.project_service import ProjectService


class ProjectInstructionsContextProvider:
    """Inject project instructions for the current session before memory context."""

    def __init__(self, project_service: ProjectService) -> None:
        self._project_service = project_service

    @property
    def context_priority(self) -> int:
        return 10

    async def get_context(self, prompt: str, turn_context: TurnContext) -> str | None:
        session_id = turn_context.session_id
        if not session_id:
            return None
        instructions = self._project_service.get_project_instructions(session_id)
        if not instructions:
            return None
        return "[Project Instructions]\n" + instructions.strip()
