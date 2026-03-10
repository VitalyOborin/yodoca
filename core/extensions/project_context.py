"""Built-in ContextProvider for project-level instructions."""

from core.extensions.contract import TurnContext
from core.extensions.router import MessageRouter


class ProjectInstructionsContextProvider:
    """Inject project instructions for the current session before memory context."""

    def __init__(self, router: MessageRouter) -> None:
        self._router = router

    @property
    def context_priority(self) -> int:
        return 10

    async def get_context(self, prompt: str, turn_context: TurnContext) -> str | None:
        session_id = turn_context.session_id
        if not session_id:
            return None
        instructions = await self._router.get_project_instructions(session_id)
        if not instructions:
            return None
        return "[Project Instructions]\n" + instructions.strip()
