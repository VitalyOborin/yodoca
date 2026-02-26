"""Executor strategy for shell command execution. Phase 1: LocalUnsafeExecutor."""

import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


class BaseExecutor(ABC):
    """Abstract executor for shell commands. Enables different isolation strategies."""

    @abstractmethod
    def execute(
        self, command: str, cwd: Path | str, timeout: int
    ) -> Tuple[int, str, str]:
        """
        Execute a shell command.
        Returns: (exit_code, stdout, stderr)
        """
        ...


class LocalUnsafeExecutor(BaseExecutor):
    """
    Phase 1: Unsafe local execution.
    Runs commands directly in the host system or current container.
    """

    def execute(
        self, command: str, cwd: Path | str, timeout: int
    ) -> Tuple[int, str, str]:
        logger.warning("UNSAFE EXECUTION: %s", command)
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired as e:
            raw = e.stdout or b""
            stdout = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            return 124, stdout, f"TimeoutError: Command exceeded {timeout} seconds."
        except Exception as e:
            return 1, "", f"ExecutionError: {str(e)}"
