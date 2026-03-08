"""Inbox extension: unified storage for incoming data from external systems."""

import sys
from pathlib import Path
from typing import Any

_ext_dir = Path(__file__).resolve().parent
if str(_ext_dir) not in sys.path:
    sys.path.insert(0, str(_ext_dir))

from agents import function_tool  # noqa: E402

from sandbox.extensions.inbox.models import (  # noqa: E402
    InboxItem,
    InboxItemInput,
    InboxListResult,
    InboxReadResult,
    InboxWriteResult,
)
from sandbox.extensions.inbox.repository import InboxRepository  # noqa: E402


class InboxExtension:
    """Extension: ToolProvider + service API for inbox storage."""

    def __init__(self) -> None:
        self._ctx: Any = None
        self._repo: InboxRepository | None = None

    async def initialize(self, context: Any) -> None:
        self._ctx = context
        db_path = context.data_dir / "inbox.db"
        self._repo = InboxRepository(db_path)
        await self._repo.ensure_conn()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        if self._repo:
            await self._repo.close()
            self._repo = None

    def health_check(self) -> bool:
        return self._repo is not None

    # --- Service API (for source extensions via context.get_extension("inbox")) ---

    async def upsert_item(self, input: InboxItemInput) -> InboxWriteResult:
        """Upsert item. Emits inbox.item.ingested unless duplicate."""
        if not self._repo:
            return InboxWriteResult(success=False, error="Inbox not initialized")
        try:
            inbox_id, change_type, ingested_at = await self._repo.upsert_item(input)
            if change_type != "duplicate":
                await self._ctx.emit(
                    "inbox.item.ingested",
                    {
                        "inbox_id": inbox_id,
                        "source_type": input.source_type,
                        "source_account": input.source_account,
                        "entity_type": input.entity_type,
                        "external_id": input.external_id,
                        "title": input.title,
                        "change_type": change_type,
                        "occurred_at": input.occurred_at,
                        "ingested_at": ingested_at,
                    },
                )
            return InboxWriteResult(
                success=True,
                inbox_id=inbox_id,
                change_type=change_type,
            )
        except Exception as e:
            return InboxWriteResult(success=False, error=str(e))

    async def get_item(self, inbox_id: int) -> InboxItem | None:
        """Get single item by id."""
        if not self._repo:
            return None
        row = await self._repo.get_item(inbox_id)
        return InboxItem.model_validate(row) if row else None

    async def list_items(
        self,
        *,
        source_type: str | None = None,
        entity_type: str | None = None,
        status: str = "active",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[InboxItem], int]:
        """List items with filters. Returns (items, total_count)."""
        if not self._repo:
            return ([], 0)
        rows, total = await self._repo.list_items(
            source_type=source_type,
            entity_type=entity_type,
            status=status,
            limit=limit,
            offset=offset,
        )
        return ([InboxItem.model_validate(r) for r in rows], total)

    async def get_cursor(
        self, source_type: str, source_account: str, stream: str
    ) -> str | None:
        """Get cursor value for (source_type, source_account, stream)."""
        if not self._repo:
            return None
        return await self._repo.get_cursor(source_type, source_account, stream)

    async def set_cursor(
        self,
        source_type: str,
        source_account: str,
        stream: str,
        value: str,
    ) -> None:
        """Set cursor value."""
        if self._repo:
            await self._repo.set_cursor(source_type, source_account, stream, value)

    # --- ToolProvider ---

    def get_tools(self) -> list[Any]:
        if not self._repo:
            return []
        ext = self

        @function_tool(name_override="inbox_list")
        async def inbox_list(
            source_type: str | None = None,
            entity_type: str | None = None,
            status: str = "active",
            limit: int = 50,
            offset: int = 0,
        ) -> InboxListResult:
            """List inbox items with optional filters.

            Use to browse incoming messages from mail, GitLab, GitHub, etc.
            Filter by source_type (e.g. mail, gitlab), entity_type (e.g. email.message),
            or status (active, deleted).
            """
            try:
                items, total = await ext.list_items(
                    source_type=source_type,
                    entity_type=entity_type,
                    status=status,
                    limit=limit,
                    offset=offset,
                )
                return InboxListResult(
                    success=True,
                    items=items,
                    total=total,
                )
            except Exception as e:
                return InboxListResult(success=False, error=str(e))

        @function_tool(name_override="inbox_read")
        async def inbox_read(inbox_id: int) -> InboxReadResult:
            """Read a single inbox item by id.

            Use inbox_id from inbox_list or from inbox.item.ingested event.
            """
            try:
                item = await ext.get_item(inbox_id)
                return InboxReadResult(success=True, item=item)
            except Exception as e:
                return InboxReadResult(success=False, error=str(e))

        return [inbox_list, inbox_read]
