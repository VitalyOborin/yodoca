"""AccountStore: JSON-backed registry of mail accounts with asyncio.Lock."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel


class AccountInfo(BaseModel):
    """Metadata for a configured mail account (non-secret)."""

    account_id: str
    provider: str
    email: str
    enabled: bool = True
    added_at: str = ""  # ISO 8601
    last_sync_at: str | None = None  # ISO 8601 or None
    initial_sync_done: bool = False


_ACCOUNTS_FILE = "accounts.json"
_ACCOUNTS_TEMP = "accounts.json.tmp"


class AccountStore:
    """JSON-backed account registry. All operations guarded by asyncio.Lock."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._path = self._data_dir / _ACCOUNTS_FILE
        self._temp_path = self._data_dir / _ACCOUNTS_TEMP
        self._lock = asyncio.Lock()

    async def _load(self) -> list[dict]:
        """Load accounts from disk. Must hold _lock."""
        if not self._path.exists():
            return []
        raw = await asyncio.to_thread(self._path.read_text, encoding="utf-8")
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    async def _save(self, accounts: list[dict]) -> None:
        """Save accounts to disk atomically. Must hold _lock."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        content = json.dumps(accounts, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self._temp_path.write_text, content, encoding="utf-8")
        await asyncio.to_thread(self._temp_path.replace, self._path)

    async def list_accounts(self) -> list[AccountInfo]:
        """Return all accounts."""
        async with self._lock:
            raw = await self._load()
            return [AccountInfo.model_validate(a) for a in raw]

    async def get_account(self, account_id: str) -> AccountInfo | None:
        """Return account by id."""
        async with self._lock:
            raw = await self._load()
            for a in raw:
                if a.get("account_id") == account_id:
                    return AccountInfo.model_validate(a)
            return None

    async def add_account(self, account: AccountInfo) -> None:
        """Add or replace account."""
        if not account.added_at:
            account = account.model_copy(
                update={"added_at": datetime.now(UTC).isoformat()}
            )
        async with self._lock:
            raw = await self._load()
            raw = [a for a in raw if a.get("account_id") != account.account_id]
            raw.append(account.model_dump(mode="json"))
            await self._save(raw)

    async def remove_account(self, account_id: str) -> bool:
        """Remove account. Returns True if found and removed."""
        async with self._lock:
            raw = await self._load()
            before = len(raw)
            raw = [a for a in raw if a.get("account_id") != account_id]
            if len(raw) < before:
                await self._save(raw)
                return True
            return False

    async def update_account(
        self, account_id: str, **updates: str | bool | None
    ) -> bool:
        """Update account fields. Returns True if found."""
        allowed = {"enabled", "last_sync_at", "initial_sync_done"}
        to_apply = {k: v for k, v in updates.items() if k in allowed}
        async with self._lock:
            raw = await self._load()
            for i, a in enumerate(raw):
                if a.get("account_id") == account_id:
                    a.update(to_apply)
                    raw[i] = a
                    await self._save(raw)
                    return True
            return False
