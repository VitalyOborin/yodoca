"""Assemble ContextProvider chain and wire invoke middleware on MessageRouter."""

from collections.abc import Callable

from core.extensions.contract import (
    ContextProvider,
    Extension,
    ExtensionState,
    TurnContext,
)
from core.extensions.routing.builtin_context import (
    ActiveChannelContextProvider,
    CapabilitiesSummaryContextProvider,
)
from core.extensions.routing.project_context import ProjectInstructionsContextProvider
from core.extensions.routing.router import MessageRouter


def wire_context_providers(
    router: MessageRouter,
    extensions: dict[str, Extension],
    state: dict[str, ExtensionState],
    get_capabilities_summary: Callable[[], str],
) -> None:
    """Collect active ContextProviders plus built-ins; set router invoke middleware.

    The middleware returns system-role context, not an enriched user prompt.
    """
    providers: list[ContextProvider] = [
        ActiveChannelContextProvider(router),
        CapabilitiesSummaryContextProvider(get_capabilities_summary),
    ]
    if router.project_service is not None:
        providers.append(ProjectInstructionsContextProvider(router.project_service))
    ext_providers = [
        ext
        for ext_id, ext in extensions.items()
        if isinstance(ext, ContextProvider)
        and state.get(ext_id, ExtensionState.INACTIVE) == ExtensionState.ACTIVE
    ]
    providers.extend(ext_providers)
    providers = sorted(providers, key=lambda p: p.context_priority)
    if not providers:
        return

    async def _middleware(prompt: str, turn_context: TurnContext) -> str:
        parts: list[str] = []
        for provider in providers:
            ctx = await provider.get_context(prompt, turn_context)
            if ctx:
                parts.append(ctx)
        if not parts:
            return ""
        return "\n\n---\n\n".join(parts)

    router.set_invoke_middleware(_middleware)
