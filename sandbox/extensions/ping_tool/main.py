"""Ping tool extension: minimal ToolProvider to verify Loader registers tools and agent can call them."""

from typing import Any

from agents import function_tool


class PingToolExtension:
    """Extension + ToolProvider: single ping tool."""

    async def initialize(self, context: Any) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        pass

    def health_check(self) -> bool:
        return True

    def get_tools(self) -> list[Any]:
        @function_tool
        def ping(message: str) -> str:
            """Ping tool. Returns pong with the message."""
            return f"pong: {message}"

        return [ping]
