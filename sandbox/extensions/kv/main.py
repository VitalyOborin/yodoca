"""Key-Value Store extension. Provides kv_set and kv_get tools backed by a JSON file in data_dir."""

import asyncio
import fnmatch
import json
import logging
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agents import function_tool
from pydantic import BaseModel, Field

# --- Tool result models (structured output per agent_tools skill) ---


class KvSetResult(BaseModel):
    """Result of kv_set tool."""

    success: bool
    key: str = ""
    status: str = "set"  # "set" | "deleted" | "error"
    error: str | None = None


class KvGetResult(BaseModel):
    """Result of kv_get tool."""

    success: bool
    key: str = ""
    value: str | None = None  # for exact match
    matches: dict[str, str] = Field(default_factory=dict)  # for pattern match
    status: str = "found"  # "found" | "not_found" | "pattern_match"
    error: str | None = None


@runtime_checkable
class KvStoreProtocol(Protocol):
    """Typed programmatic KV API for extensions via context.get_extension('kv')."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str | None) -> None: ...

    async def get_matching(self, pattern: str) -> dict[str, str]: ...


_DATA_FILE = "values.json"
_TEMP_FILE = "values.json.tmp"


def _parse_max_entries(raw: Any) -> int:
    """Resolve max_entries from config; invalid or non-positive falls back to 500."""
    if raw is None:
        return 500
    try:
        n = int(raw)
        return n if n > 0 else 500
    except (TypeError, ValueError):
        return 500


class _FileStore:
    """JSON file-backed key-value store. Atomic writes via temp file + replace."""

    def __init__(
        self,
        data_dir: Path,
        namespace: str,
        logger: logging.Logger,
        max_entries: int,
    ) -> None:
        self._data_dir = data_dir
        self._namespace = (namespace or "").strip()
        self._path = data_dir / _DATA_FILE
        self._temp_path = data_dir / _TEMP_FILE
        self._healthy = False
        self._logger = logger
        self._max_entries = max_entries
        self._lock = asyncio.Lock()

    def _ns(self, key: str) -> str:
        return f"{self._namespace}:{key}" if self._namespace else key

    def _to_user_key(self, stored_key: str) -> str:
        """Convert stored key (with optional namespace prefix) to user-facing key."""
        if self._namespace and stored_key.startswith(f"{self._namespace}:"):
            return stored_key[len(self._namespace) + 1 :]
        return stored_key

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            self._logger.warning(
                "KV store file corrupt or unreadable, starting fresh: %s", exc
            )
            return {}

    def _save(self, store: dict[str, str]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._temp_path.write_text(
            json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._temp_path.replace(self._path)

    def initialize(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._healthy = True

    async def get(self, key: str) -> str | None:
        async with self._lock:
            store = await asyncio.to_thread(self._load)
            return store.get(self._ns(key.strip()))

    async def get_matching(self, pattern: str) -> dict[str, str]:
        """Return all key-value pairs where user-facing key matches the glob pattern.
        Pattern uses * (any chars) and ? (single char), e.g. 'key:*', '*what*', 'start*:part*'.
        """
        async with self._lock:
            store = await asyncio.to_thread(self._load)
            result: dict[str, str] = {}
            for stored_key, value in store.items():
                user_key = self._to_user_key(stored_key)
                if fnmatch.fnmatch(user_key, pattern):
                    result[user_key] = value
            return dict(sorted(result.items()))

    async def set(self, key: str, value: str | None) -> None:
        async with self._lock:
            store = await asyncio.to_thread(self._load)
            ns_key = self._ns(key.strip())
            if value is None:
                store.pop(ns_key, None)
            else:
                if ns_key not in store and len(store) >= self._max_entries:
                    raise ValueError(
                        f"KV store entry limit reached ({self._max_entries} entries)."
                    )
                store[ns_key] = value
            await asyncio.to_thread(self._save, store)

    async def close(self) -> None:
        self._healthy = False

    def is_healthy(self) -> bool:
        return self._healthy


class KvExtension:
    """Extension + ToolProvider: persistent key-value store for the agent."""

    def __init__(self) -> None:
        self._store: _FileStore | None = None
        self._context: Any = None

    async def initialize(self, context: Any) -> None:
        self._context = context
        namespace = context.get_config("namespace", "") or ""
        max_entries = _parse_max_entries(context.get_config("max_entries", None))
        self._store = _FileStore(
            context.data_dir,
            namespace,
            context.logger,
            max_entries,
        )
        self._store.initialize()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        if self._store:
            await self._store.close()
            self._store = None

    def health_check(self) -> bool:
        return self._store is not None and self._store.is_healthy()

    def _require_store(self) -> _FileStore:
        """Return initialized store or raise a consistent runtime error."""
        if not self._store:
            raise RuntimeError("KV store not initialized")
        return self._store

    async def get(self, key: str) -> str | None:
        """Return value for key (for other extensions via context.get_extension('kv'))."""
        return await self._require_store().get(key)

    async def get_matching(self, pattern: str) -> dict[str, str]:
        """Return all key-value pairs matching the glob pattern (for other extensions)."""
        return await self._require_store().get_matching(pattern)

    async def set(self, key: str, value: str | None) -> None:
        """Write or delete key (for other extensions via context.get_extension('kv'))."""
        await self._require_store().set(key, value)

    def _build_kv_set_tool(self, store: _FileStore) -> Any:
        @function_tool(name_override="kv_set")
        async def kv_set(
            key: str,
            value: str = "",
        ) -> KvSetResult:
            """Store a value under key in the persistent key-value store.

            Pass an empty string or omit value to delete the key.

            Args:
                key: Non-empty key name. Use alphanumeric, underscore, hyphen for best compatibility.
                value: String value to store. Empty string to delete.
            """
            if not key or not key.strip():
                return KvSetResult(
                    success=False,
                    status="error",
                    error="key must be a non-empty string.",
                )

            normalized_key = key.strip()
            val: str | None = value.strip() if value else None
            if val == "":
                val = None

            try:
                await store.set(normalized_key, val)
            except ValueError as exc:
                return KvSetResult(
                    success=False,
                    status="error",
                    error=str(exc),
                )

            if val is None:
                return KvSetResult(success=True, key=normalized_key, status="deleted")
            return KvSetResult(success=True, key=normalized_key, status="set")

        return kv_set

    def _build_kv_get_tool(self, store: _FileStore) -> Any:
        @function_tool(name_override="kv_get")
        async def kv_get(key: str) -> KvGetResult:
            """Retrieve value(s) from the persistent key-value store.

            Accepts an exact key or a glob pattern with wildcards:
            - * matches any sequence of characters
            - ? matches any single character
            Examples: 'key:*', '*what*', 'agent_health.*', 'start*:part*'

            Args:
                key: Non-empty key name or glob pattern (e.g. 'my_key', 'prefix:*').
            """
            if not key or not key.strip():
                return KvGetResult(
                    success=False,
                    status="error",
                    error="key must be a non-empty string.",
                )
            normalized_key = key.strip()
            if "*" in normalized_key or "?" in normalized_key:
                matches = await store.get_matching(normalized_key)
                if not matches:
                    return KvGetResult(
                        success=False,
                        key=normalized_key,
                        status="not_found",
                        error=f"No keys matching pattern '{normalized_key}'.",
                    )
                return KvGetResult(
                    success=True,
                    key=normalized_key,
                    matches=matches,
                    status="pattern_match",
                )
            result = await store.get(normalized_key)
            if result is None:
                return KvGetResult(
                    success=False,
                    key=normalized_key,
                    status="not_found",
                    error=f"Key '{normalized_key}' not found.",
                )
            return KvGetResult(
                success=True,
                key=normalized_key,
                value=result,
                status="found",
            )

        return kv_get

    def get_tools(self) -> list[Any]:
        if not self._store:
            return []
        store = self._store
        return [self._build_kv_set_tool(store), self._build_kv_get_tool(store)]
