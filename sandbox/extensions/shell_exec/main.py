"""Shell Exec extension: ToolProvider for executing shell commands with CWD tracking."""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, List

_ext_dir = Path(__file__).resolve().parent
if str(_ext_dir) not in sys.path:
    sys.path.insert(0, str(_ext_dir))

from agents import function_tool
from pydantic import BaseModel

from executors import BaseExecutor, LocalUnsafeExecutor

logger = logging.getLogger(__name__)


class ShellExecResult(BaseModel):
    """Structured result of execute_shell_command."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


class ShellExecExtension:
    """Extension providing shell execution tool with stateful CWD tracking."""

    def __init__(self) -> None:
        self._ctx: Any = None
        self._executor: BaseExecutor | None = None
        self._timeout: int = 60
        self._max_output: int = 8000
        self._containered: bool = False
        self._current_cwd: Path | None = None
        self._sandbox_root: Path | None = None

    async def initialize(self, context: Any) -> None:
        self._ctx = context
        self._containered = context.get_config("containered", False)
        self._timeout = context.get_config("timeout_seconds", 60)
        self._max_output = context.get_config("max_output_length", 8000)
        self._sandbox_root = context.data_dir.parent.parent

        cwd_config = context.get_config("cwd", None)
        if cwd_config is None or cwd_config == "":
            self._current_cwd = context.data_dir
        else:
            resolved = (self._sandbox_root / str(cwd_config)).resolve()
            try:
                resolved.relative_to(self._sandbox_root.resolve())
            except ValueError:
                logger.warning(
                    "cwd %r outside sandbox, falling back to data_dir",
                    cwd_config,
                )
                resolved = context.data_dir
            resolved.mkdir(parents=True, exist_ok=True)
            self._current_cwd = resolved

        if self._containered:
            logger.error("Containered mode not implemented yet. Using unsafe local.")
        self._executor = LocalUnsafeExecutor()

        logger.info("ShellExec initialized. containered=%s", self._containered)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        pass

    def health_check(self) -> bool:
        return True

    def get_tools(self) -> List[Any]:
        return [
            function_tool(name_override="execute_shell_command")(
                self.execute_shell_command
            )
        ]

    def _truncate_head_tail(self, text: str) -> str:
        """Keep first and last halves with middle marker. LLM sees start and end."""
        if len(text) <= self._max_output:
            return text
        half = self._max_output // 2
        return text[:half] + "\n...[TRUNCATED]...\n" + text[-half:]

    _CWD_MARKER = "__SHELL_EXEC_CWD__"

    def _wrap_command_with_pwd(self, command: str) -> str:
        """Append marker + pwd/cd so we capture new cwd and split output reliably."""
        marker = self._CWD_MARKER
        if sys.platform == "win32":
            return f"{command} & echo {marker} & cd"
        return f"{command} ; echo {marker} ; pwd"

    def _parse_new_cwd(self, stdout: str) -> Path | None:
        """Extract path after CWD marker; validate it is within sandbox."""
        if self._CWD_MARKER not in stdout:
            return None
        parts = stdout.split(self._CWD_MARKER, 1)
        if len(parts) < 2:
            return None
        after = parts[1].strip().splitlines()
        if not after:
            return None
        last = after[0].strip()
        try:
            candidate = Path(last).resolve()
            if self._sandbox_root:
                candidate.relative_to(self._sandbox_root.resolve())
            return candidate
        except (ValueError, OSError):
            return None

    def _strip_pwd_from_stdout(self, stdout: str) -> str:
        """Remove CWD marker and path from stdout for agent display."""
        if self._CWD_MARKER not in stdout:
            return stdout
        parts = stdout.split(self._CWD_MARKER, 1)
        return parts[0].rstrip()

    async def execute_shell_command(self, command: str) -> ShellExecResult:
        """
        Execute a shell command on the local machine. Use this to run scripts,
        manage files, or execute code (e.g. python -c "print('hello')").
        CWD is preserved across calls (e.g. cd my_folder then cat my_file.txt).

        Args:
            command: The shell command to execute.
        """
        if not self._executor or not self._ctx or self._current_cwd is None:
            return ShellExecResult(
                exit_code=1,
                stdout="",
                stderr="ExecutionError: Extension not initialized.",
            )

        wrapped = self._wrap_command_with_pwd(command)
        cwd = str(self._current_cwd)

        try:
            exit_code, stdout, stderr = await asyncio.to_thread(
                self._executor.execute,
                wrapped,
                cwd,
                self._timeout,
            )
        except Exception as e:
            return ShellExecResult(
                exit_code=1,
                stdout="",
                stderr=f"ExecutionError: {str(e)}",
            )

        new_cwd = self._parse_new_cwd(stdout)
        if new_cwd is not None:
            self._current_cwd = new_cwd
        stdout_clean = self._strip_pwd_from_stdout(stdout)

        return ShellExecResult(
            exit_code=exit_code,
            stdout=self._truncate_head_tail(stdout_clean),
            stderr=self._truncate_head_tail(stderr),
        )
