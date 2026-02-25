"""Shell command execution tool for the AI agent using OpenAI Agents SDK ShellTool."""

import asyncio
import os
from pathlib import Path

from agents import (
    ShellCallOutcome,
    ShellCommandOutput,
    ShellCommandRequest,
    ShellResult,
    ShellTool,
)

from core.tools.sandbox import SANDBOX_DIR

_DEFAULT_TIMEOUT_SECONDS = 60


class SandboxShellExecutor:
    """ShellExecutor that runs commands in the sandbox directory only."""

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = (cwd or SANDBOX_DIR).resolve()
        self._cwd.mkdir(parents=True, exist_ok=True)

    async def __call__(self, request: ShellCommandRequest) -> ShellResult:
        action = request.data.action

        outputs: list[ShellCommandOutput] = []
        for command in action.commands:
            timed_out = False
            try:
                timeout_sec = (action.timeout_ms or 0) / 1000 or _DEFAULT_TIMEOUT_SECONDS
                proc = await asyncio.create_subprocess_shell(
                    command,
                    cwd=self._cwd,
                    env=os.environ.copy(),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(), timeout=timeout_sec
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    stdout_bytes, stderr_bytes = await proc.communicate()
                    timed_out = True

                stdout = stdout_bytes.decode("utf-8", errors="ignore")
                stderr = stderr_bytes.decode("utf-8", errors="ignore")
                outputs.append(
                    ShellCommandOutput(
                        command=command,
                        stdout=stdout,
                        stderr=stderr,
                        outcome=ShellCallOutcome(
                            type="timeout" if timed_out else "exit",
                            exit_code=getattr(proc, "returncode", None),
                        ),
                    )
                )

                if timed_out:
                    break

            except Exception as e:
                outputs.append(
                    ShellCommandOutput(
                        command=command,
                        stdout="",
                        stderr=str(e),
                        outcome=ShellCallOutcome(type="exit", exit_code=None),
                    )
                )
                break

        return ShellResult(
            output=outputs,
            provider_data={"working_directory": str(self._cwd)},
        )


# ShellTool with local executor, sandbox-scoped
shell_tool = ShellTool(
    executor=SandboxShellExecutor(),
    environment={"type": "local"},
)
