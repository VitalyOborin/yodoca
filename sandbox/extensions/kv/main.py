"""Key-Value Store extension. Provides kv_set and kv_get tools backed by a JSON file in data_dir."""

import json
from pathlib import Path
from typing import Annotated, Any

from agents import function_tool
from pydantic import Field

_DATA_FILE = "values.json"
_TEMP_FILE = "values.json.tmp"


class _FileStore:
    """JSON file-backed key-value store. Atomic writes via temp file + replace."""

    def __init__(self, data_dir: Path, namespace: str) -> None:
        self._data_dir = data_dir
        self._namespace = (namespace or "").strip()
        self._path = data_dir / _DATA_FILE
        self._temp_path = data_dir / _TEMP_FILE
        self._healthy = False

    def _ns(self, key: str) -> str:
        return f"{self._namespace}:{key}" if self._namespace else key

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
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
        store = self._load()
        return store.get(self._ns(key.strip()))

    async def set(self, key: str, value: str | None) -> None:
        store = self._load()
        ns_key = self._ns(key.strip())
        if value is None:
            store.pop(ns_key, None)
        else:
            store[ns_key] = value
        self._save(store)

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
        self._store = _FileStore(context.data_dir, namespace)
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

    async def get(self, key: str) -> str | None:
        """Return value for key (for other extensions via context.get_extension('kv'))."""
        if not self._store:
            raise RuntimeError("KV store not initialized")
        return await self._store.get(key)

    async def set(self, key: str, value: str | None) -> None:
        """Write or delete key (for other extensions via context.get_extension('kv'))."""
        if not self._store:
            raise RuntimeError("KV store not initialized")
        await self._store.set(key, value)

    def get_tools(self) -> list[Any]:
        if not self._store:
            return []
        store = self._store

        @function_tool(name_override="kv_set")
        async def kv_set(
            key: Annotated[str, Field(min_length=1)],
            value: str = "",
        ) -> str:
            """Store a value under key in the persistent key-value store.

            Pass an empty string or omit value to delete the key. Returns a confirmation message.

            Args:
                key: Non-empty key name. Use alphanumeric, underscore, hyphen for best compatibility.
                value: Value to store. Empty string or omit to delete the key.
            """
            if not key or not key.strip():
                return "Error: key must be a non-empty string."
            val: str | None = value.strip() if value else None
            if val == "":
                val = None
            await store.set(key.strip(), val)
            if val is None:
                return f"Key '{key.strip()}' deleted."
            return f"Key '{key.strip()}' set."

        @function_tool(name_override="kv_get")
        async def kv_get(key: Annotated[str, Field(min_length=1)]) -> str:
            """Retrieve the value stored under key from the persistent key-value store.

            Returns the stored value, or a message indicating the key does not exist.

            Args:
                key: Non-empty key name to look up.
            """
            if not key or not key.strip():
                return "Error: key must be a non-empty string."
            result = await store.get(key.strip())
            if result is None:
                return f"Key '{key.strip()}' not found."
            return result

        return [kv_set, kv_get]
