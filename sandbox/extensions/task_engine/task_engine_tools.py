"""Task Engine tools: build Orchestrator tool list."""

from typing import Any

from agents import function_tool

from sandbox.extensions.task_engine.models import (
    ActiveTasksResult,
    CancelTaskResult,
    ChainStatusResult,
    ChainStep,
    SubmitChainResult,
    SubmitTaskResult,
    TaskStatusResult,
)


def build_tools(ext: Any) -> list[Any]:
    """Build task engine tools that delegate to the extension."""

    @function_tool
    async def submit_task(
        goal: str,
        agent_id: str = "orchestrator",
        priority: int = 5,
        parent_task_id: str | None = None,
        after_task_id: str | None = None,
        max_steps: int | None = None,
        output_channel: str | None = None,
    ) -> SubmitTaskResult:
        """Submit a new background task for async execution by a specified agent.

        Use when:
        - The task requires multiple steps (research, generation, analysis)
        - The task should run in the background while the user continues chatting
        - The task needs a specialized agent

        agent_id: 'orchestrator' (default) or any agent from list_agents.
        after_task_id: optional; task will run only after this predecessor completes
        (for ad-hoc chaining). Use submit_chain for multi-step pipelines.
        Returns task_id for tracking.
        output_channel: optional target channel id for final delivery
        (e.g. 'telegram_channel', 'cli_channel').
        """
        return await ext.submit_task(
            goal,
            agent_id,
            priority,
            parent_task_id,
            after_task_id,
            max_steps,
            output_channel,
        )

    @function_tool
    async def submit_chain(
        steps: list[ChainStep],
        priority: int = 5,
        output_channel: str | None = None,
    ) -> SubmitChainResult:
        """Submit a sequence of tasks that execute one after another.

        Each step's result is passed as context to the next step.
        Use for multi-phase workflows like: research -> draft -> review."""
        return await ext.submit_chain(steps, priority, output_channel)

    @function_tool
    async def get_chain_status(chain_id: str) -> ChainStatusResult:
        """Get status of all tasks in a chain, in execution order."""
        return await ext._get_chain_status(chain_id)

    @function_tool
    async def get_task_status(task_id: str) -> TaskStatusResult:
        """Get current status, progress, and partial result of a background task."""
        return await ext._get_task_status(task_id)

    @function_tool
    async def list_active_tasks() -> ActiveTasksResult:
        """List all running and pending tasks with statuses and progress."""
        return await ext._list_active_tasks()

    @function_tool
    async def cancel_task(task_id: str, reason: str = "") -> CancelTaskResult:
        """Cancel a running or pending task.
        Cancellation takes effect between steps; the current step (if any) completes first."""
        return await ext._cancel_task(task_id, reason)

    @function_tool
    async def request_human_review(task_id: str, question: str) -> SubmitTaskResult:
        """Pause a running task and ask the user for input.
        Use when the task needs a decision, clarification, or approval from the user.
        The task will be paused until the user responds."""
        return await ext._request_human_review(task_id, question)

    @function_tool
    async def respond_to_review(task_id: str, response: str) -> SubmitTaskResult:
        """Provide user's response to a paused task.
        Call when the user replies to a human_review question."""
        return await ext._respond_to_review(task_id, response)

    return [
        submit_task,
        submit_chain,
        get_chain_status,
        get_task_status,
        list_active_tasks,
        cancel_task,
        request_human_review,
        respond_to_review,
    ]
