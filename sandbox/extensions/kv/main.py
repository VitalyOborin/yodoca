"""Key-Value Store extension. Provides kv_set and kv_get tools backed by a JSON file in data_dir."""

import fnmatch
import json
from pathlib import Path
from typing import Any

from agents import function_tool

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

    async def get_matching(self, pattern: str) -> dict[str, str]:
        """Return all key-value pairs where user-facing key matches the glob pattern.
        Pattern uses * (any chars) and ? (single char), e.g. 'key:*', '*what*', 'start*:part*'.
        """
        store = self._load()
        result: dict[str, str] = {}
        for stored_key, value in store.items():
            user_key = self._to_user_key(stored_key)
            if fnmatch.fnmatch(user_key, pattern):
                result[user_key] = value
        return dict(sorted(result.items()))

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

    async def get_matching(self, pattern: str) -> dict[str, str]:
        """Return all key-value pairs matching the glob pattern (for other extensions)."""
        if not self._store:
            raise RuntimeError("KV store not initialized")
        return await self._store.get_matching(pattern)

    async def set(self, key: str, value: str | None) -> None:
        """Write or delete key (for other extensions via context.get_extension('kv'))."""
        if not self._store:
            raise RuntimeError("KV store not initialized")
        await self._store.set(key, value)

    def get_tools(self) -> list[Any]:
        if not self._store:
            return []
        store = self._store

        @function_tool(name_override="kv_set", strict_mode=False)
        async def kv_set(
            key: str,
            value: Any = "",
        ) -> str:
            """Store a value under key in the persistent key-value store.

            Pass an empty string or omit value to delete the key. Returns a confirmation message.

            Args:
                key: Non-empty key name. Use alphanumeric, underscore, hyphen for best compatibility.
                value: Value to store. Can be string, number, or boolean. Empty string to delete.
            """
            if not key or not key.strip():
                return "Error: key must be a non-empty string."

            # Если модель передала число (например, 444), конвертируем его в строку
            if value is not None:
                value = str(value)

            # Пустая строка расценивается как команда на удаление (None)
            val: str | None = value.strip() if value else None
            if val == "":
                val = None

            await store.set(key.strip(), val)
            
            if val is None:
                return f"Key '{key.strip()}' deleted."
            return f"Key '{key.strip()}' set."

        @function_tool(name_override="kv_get")
        async def kv_get(key: str) -> str:
            """Retrieve value(s) from the persistent key-value store.

            Accepts an exact key or a glob pattern with wildcards:
            - * matches any sequence of characters
            - ? matches any single character
            Examples: 'key:*', '*what*', 'agent_health.*', 'start*:part*'

            Returns the stored value for exact match, or key: value lines for pattern match.
            Returns a message if no keys match.

            Args:
                key: Non-empty key name or glob pattern (e.g. 'my_key', 'prefix:*').
            """
            if not key or not key.strip():
                return "Error: key must be a non-empty string."
            k = key.strip()
            if "*" in k or "?" in k:
                matches = await store.get_matching(k)
                if not matches:
                    return f"No keys matching pattern '{k}'."
                lines = [f"{mk}: {mv}" for mk, mv in matches.items()]
                return "\n".join(lines)
            result = await store.get(k)
            if result is None:
                return f"Key '{k}' not found."
            return result

        return [kv_set, kv_get]
