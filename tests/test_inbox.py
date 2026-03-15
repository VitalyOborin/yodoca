"""Tests for Inbox extension: repository, extension, and tools."""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sandbox.extensions.inbox.main import InboxExtension
from sandbox.extensions.inbox.models import InboxItemInput
from sandbox.extensions.inbox.repository import InboxRepository


def _make_tool_ctx(tool_name: str, tool_arguments: str):
    from agents.tool_context import ToolContext

    return ToolContext(
        context=object(),
        tool_name=tool_name,
        tool_call_id="test-call-id",
        tool_arguments=tool_arguments,
    )


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "inbox.db"


@pytest.fixture
async def repo(tmp_db: Path) -> InboxRepository:
    r = InboxRepository(tmp_db)
    await r.ensure_conn()
    yield r
    await r.close()


class TestInboxRepository:
    """Test InboxRepository methods."""

    @pytest.mark.asyncio
    async def test_upsert_new_item(self, repo: InboxRepository) -> None:
        inp = InboxItemInput(
            source_type="mail",
            source_account="default",
            entity_type="email.message",
            external_id="msg-1",
            title="Test email",
            occurred_at=time.time(),
            payload={"from": "a@b.com", "subject": "Hi"},
        )
        inbox_id, change_type, ingested_at = await repo.upsert_item(inp)
        assert inbox_id > 0
        assert change_type == "created"
        assert ingested_at > 0
        row = await repo.get_item(inbox_id)
        assert row is not None
        assert row["external_id"] == "msg-1"
        assert row["payload"]["from"] == "a@b.com"
        assert row["is_read"] is False

    @pytest.mark.asyncio
    async def test_upsert_duplicate_suppression(self, repo: InboxRepository) -> None:
        inp = InboxItemInput(
            source_type="mail",
            source_account="default",
            entity_type="email.message",
            external_id="msg-dup",
            title="Same",
            occurred_at=time.time(),
            payload={"x": 1},
        )
        inbox_id1, ct1, _ = await repo.upsert_item(inp)
        assert ct1 == "created"
        inbox_id2, ct2, ingested2 = await repo.upsert_item(inp)
        assert ct2 == "duplicate"
        assert inbox_id2 == inbox_id1
        assert ingested2 == 0

    @pytest.mark.asyncio
    async def test_upsert_mutable_update(self, repo: InboxRepository) -> None:
        inp1 = InboxItemInput(
            source_type="gitlab",
            source_account="default",
            entity_type="gitlab.merge_request",
            external_id="mr-1",
            title="MR v1",
            occurred_at=time.time(),
            payload={"state": "opened"},
        )
        id1, ct1, _ = await repo.upsert_item(inp1)
        assert ct1 == "created"
        inp2 = InboxItemInput(
            source_type="gitlab",
            source_account="default",
            entity_type="gitlab.merge_request",
            external_id="mr-1",
            title="MR v2",
            occurred_at=time.time(),
            payload={"state": "merged"},
        )
        id2, ct2, _ = await repo.upsert_item(inp2)
        assert ct2 == "updated"
        assert id2 != id1
        row_old = await repo.get_item(id1)
        assert row_old["is_current"] is False
        row_new = await repo.get_item(id2)
        assert row_new["is_current"] is True
        assert row_new["payload"]["state"] == "merged"

    @pytest.mark.asyncio
    async def test_upsert_soft_delete(self, repo: InboxRepository) -> None:
        inp = InboxItemInput(
            source_type="mail",
            source_account="default",
            entity_type="email.message",
            external_id="msg-del",
            title="To delete",
            occurred_at=time.time(),
            payload={},
        )
        inbox_id, ct1, _ = await repo.upsert_item(inp)
        assert ct1 == "created"
        inp_del = InboxItemInput(
            source_type="mail",
            source_account="default",
            entity_type="email.message",
            external_id="msg-del",
            title="Deleted",
            occurred_at=time.time(),
            status="deleted",
            payload={},
        )
        id_del, ct2, _ = await repo.upsert_item(inp_del)
        assert ct2 == "deleted"
        assert id_del == inbox_id
        row = await repo.get_item(inbox_id)
        assert row["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_upsert_soft_delete_updates_ingested_at(
        self, repo: InboxRepository
    ) -> None:
        inp = InboxItemInput(
            source_type="mail",
            source_account="default",
            entity_type="email.message",
            external_id="msg-del-ts",
            title="To delete",
            occurred_at=time.time(),
            payload={},
        )
        inbox_id, _, created_ingested_at = await repo.upsert_item(inp)
        await asyncio.sleep(0.01)
        inp_del = InboxItemInput(
            source_type="mail",
            source_account="default",
            entity_type="email.message",
            external_id="msg-del-ts",
            title="Deleted",
            occurred_at=time.time(),
            status="deleted",
            payload={},
        )
        _, ct2, deleted_ingested_at = await repo.upsert_item(inp_del)
        assert ct2 == "deleted"
        assert deleted_ingested_at > created_ingested_at
        row = await repo.get_item(inbox_id)
        assert row is not None
        assert row["ingested_at"] == deleted_ingested_at

    @pytest.mark.asyncio
    async def test_upsert_soft_delete_duplicate_suppression(
        self, repo: InboxRepository
    ) -> None:
        inp = InboxItemInput(
            source_type="mail",
            source_account="default",
            entity_type="email.message",
            external_id="msg-del-dup",
            title="To delete",
            occurred_at=time.time(),
            payload={},
        )
        inbox_id, _, _ = await repo.upsert_item(inp)
        inp_del = InboxItemInput(
            source_type="mail",
            source_account="default",
            entity_type="email.message",
            external_id="msg-del-dup",
            title="Deleted",
            occurred_at=time.time(),
            status="deleted",
            payload={},
        )
        id_del_1, ct_1, _ = await repo.upsert_item(inp_del)
        id_del_2, ct_2, ingested_2 = await repo.upsert_item(inp_del)
        assert id_del_1 == inbox_id
        assert ct_1 == "deleted"
        assert id_del_2 == inbox_id
        assert ct_2 == "duplicate"
        assert ingested_2 == 0.0

    @pytest.mark.asyncio
    async def test_get_item(self, repo: InboxRepository) -> None:
        inp = InboxItemInput(
            source_type="mail",
            source_account="a",
            entity_type="email.message",
            external_id="e1",
            title="T",
            occurred_at=time.time(),
            payload={"k": "v"},
        )
        inbox_id, _, _ = await repo.upsert_item(inp)
        row = await repo.get_item(inbox_id)
        assert row is not None
        assert row["id"] == inbox_id
        assert row["payload"] == {"k": "v"}
        assert await repo.get_item(99999) is None

    @pytest.mark.asyncio
    async def test_list_items_filters(self, repo: InboxRepository) -> None:
        for i in range(3):
            await repo.upsert_item(
                InboxItemInput(
                    source_type="mail",
                    source_account="acc",
                    entity_type="email.message",
                    external_id=f"e{i}",
                    title=f"T{i}",
                    occurred_at=time.time(),
                    payload={},
                )
            )
        await repo.upsert_item(
            InboxItemInput(
                source_type="gitlab",
                source_account="acc",
                entity_type="gitlab.merge_request",
                external_id="mr1",
                title="MR",
                occurred_at=time.time(),
                payload={},
            )
        )
        rows, total = await repo.list_items(source_type="mail")
        assert len(rows) == 3
        assert total == 3
        rows, total = await repo.list_items(source_type="gitlab")
        assert len(rows) == 1
        assert total == 1

    @pytest.mark.asyncio
    async def test_mark_read_and_unread_filter(self, repo: InboxRepository) -> None:
        inp = InboxItemInput(
            source_type="mail",
            source_account="acc",
            entity_type="email.message",
            external_id="read-filter-1",
            title="Unread item",
            occurred_at=time.time(),
            payload={},
        )
        inbox_id, _, _ = await repo.upsert_item(inp)
        assert await repo.get_unread_count() == 1

        marked = await repo.mark_read(inbox_id)
        assert marked is True
        assert await repo.get_unread_count() == 0

        unread_rows, unread_total = await repo.list_items(is_read=False)
        assert unread_total == 0
        assert unread_rows == []

        read_rows, read_total = await repo.list_items(is_read=True)
        assert read_total == 1
        assert len(read_rows) == 1
        assert read_rows[0]["id"] == inbox_id

    @pytest.mark.asyncio
    async def test_mark_all_read_with_source_filter(
        self, repo: InboxRepository
    ) -> None:
        await repo.upsert_item(
            InboxItemInput(
                source_type="mail",
                source_account="acc",
                entity_type="email.message",
                external_id="bulk-mail",
                title="Mail",
                occurred_at=time.time(),
                payload={},
            )
        )
        await repo.upsert_item(
            InboxItemInput(
                source_type="gitlab",
                source_account="acc",
                entity_type="gitlab.merge_request",
                external_id="bulk-gitlab",
                title="MR",
                occurred_at=time.time(),
                payload={},
            )
        )

        updated_mail = await repo.mark_all_read("mail")
        assert updated_mail == 1
        assert await repo.get_unread_count() == 1

        updated_rest = await repo.mark_all_read(None)
        assert updated_rest == 1
        assert await repo.get_unread_count() == 0

    @pytest.mark.asyncio
    async def test_upsert_update_inherits_is_read(self, repo: InboxRepository) -> None:
        inp1 = InboxItemInput(
            source_type="gitlab",
            source_account="default",
            entity_type="gitlab.merge_request",
            external_id="mr-read-inherit",
            title="MR v1",
            occurred_at=time.time(),
            payload={"state": "opened"},
        )
        id1, _, _ = await repo.upsert_item(inp1)
        assert await repo.mark_read(id1) is True

        inp2 = InboxItemInput(
            source_type="gitlab",
            source_account="default",
            entity_type="gitlab.merge_request",
            external_id="mr-read-inherit",
            title="MR v2",
            occurred_at=time.time(),
            payload={"state": "merged"},
        )
        id2, ct2, _ = await repo.upsert_item(inp2)
        assert ct2 == "updated"
        assert id2 != id1

        old_row = await repo.get_item(id1)
        new_row = await repo.get_item(id2)
        assert old_row is not None
        assert new_row is not None
        assert old_row["is_current"] is False
        assert new_row["is_current"] is True
        assert new_row["is_read"] is True

    @pytest.mark.asyncio
    async def test_cursor_lifecycle(self, repo: InboxRepository) -> None:
        val = await repo.get_cursor("mail", "acc", "inbox")
        assert val is None
        await repo.set_cursor("mail", "acc", "inbox", "cursor-123")
        val = await repo.get_cursor("mail", "acc", "inbox")
        assert val == "cursor-123"


class TestInboxExtension:
    """Test InboxExtension lifecycle and event emission."""

    @pytest.mark.asyncio
    async def test_upsert_emits_event(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "inbox"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = InboxExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        inp = InboxItemInput(
            source_type="mail",
            source_account="default",
            entity_type="email.message",
            external_id="ev-1",
            title="Event test",
            occurred_at=time.time(),
            payload={"x": 1},
        )
        result = await ext.upsert_item(inp)
        assert result.success is True
        assert result.change_type == "created"
        ctx.emit.assert_called_once()
        call_args = ctx.emit.call_args
        assert call_args[0][0] == "inbox.item.ingested"
        payload = call_args[0][1]
        assert payload["inbox_id"] == result.inbox_id
        assert payload["source_type"] == "mail"
        assert payload["change_type"] == "created"
        await ext.destroy()

    @pytest.mark.asyncio
    async def test_upsert_duplicate_no_event(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "inbox"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = InboxExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        inp = InboxItemInput(
            source_type="mail",
            source_account="default",
            entity_type="email.message",
            external_id="dup-1",
            title="Dup",
            occurred_at=time.time(),
            payload={"same": True},
        )
        await ext.upsert_item(inp)
        ctx.emit.reset_mock()
        result = await ext.upsert_item(inp)
        assert result.change_type == "duplicate"
        ctx.emit.assert_not_called()
        await ext.destroy()

    @pytest.mark.asyncio
    async def test_soft_delete_duplicate_no_event(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "inbox"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = InboxExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        inp = InboxItemInput(
            source_type="mail",
            source_account="default",
            entity_type="email.message",
            external_id="soft-del-dup-1",
            title="Del me",
            occurred_at=time.time(),
            payload={"same": True},
        )
        await ext.upsert_item(inp)
        inp_del = InboxItemInput(
            source_type="mail",
            source_account="default",
            entity_type="email.message",
            external_id="soft-del-dup-1",
            title="Deleted",
            occurred_at=time.time(),
            status="deleted",
            payload={"same": True},
        )
        await ext.upsert_item(inp_del)
        ctx.emit.reset_mock()
        result = await ext.upsert_item(inp_del)
        assert result.change_type == "duplicate"
        ctx.emit.assert_not_called()
        await ext.destroy()

    @pytest.mark.asyncio
    async def test_health_check(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "inbox"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = InboxExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        await ext.initialize(ctx)
        assert ext.health_check() is True
        await ext.destroy()
        assert ext.health_check() is False


class TestInboxTools:
    """Test inbox_list and inbox_read tools."""

    @pytest.mark.asyncio
    async def test_inbox_list_tool(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "inbox"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = InboxExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        await ext.upsert_item(
            InboxItemInput(
                source_type="mail",
                source_account="a",
                entity_type="email.message",
                external_id="e1",
                title="Test",
                occurred_at=time.time(),
                payload={},
            )
        )
        tools = ext.get_tools()
        inbox_list = next(t for t in tools if getattr(t, "name", None) == "inbox_list")
        args = json.dumps(
            {"source_type": "mail", "status": "active", "limit": 10, "offset": 0},
            ensure_ascii=False,
        )
        result = await inbox_list.on_invoke_tool(
            _make_tool_ctx(inbox_list.name, args), args
        )
        assert result.success is True
        assert result.total >= 1
        assert len(result.items) >= 1
        assert result.items[0].source_type == "mail"
        assert result.error is None
        await ext.destroy()

    @pytest.mark.asyncio
    async def test_inbox_read_tool(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "inbox"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = InboxExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        wr = await ext.upsert_item(
            InboxItemInput(
                source_type="mail",
                source_account="a",
                entity_type="email.message",
                external_id="read-1",
                title="Read me",
                occurred_at=time.time(),
                payload={"body": "hello"},
            )
        )
        tools = ext.get_tools()
        inbox_read = next(t for t in tools if getattr(t, "name", None) == "inbox_read")
        args = json.dumps({"inbox_id": wr.inbox_id}, ensure_ascii=False)
        result = await inbox_read.on_invoke_tool(
            _make_tool_ctx(inbox_read.name, args), args
        )
        assert result.success is True
        assert result.item is not None
        assert result.item.id == wr.inbox_id
        assert result.item.payload == {"body": "hello"}
        assert result.error is None
        await ext.destroy()

    @pytest.mark.asyncio
    async def test_inbox_read_not_found(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "inbox"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = InboxExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        tools = ext.get_tools()
        inbox_read = next(t for t in tools if getattr(t, "name", None) == "inbox_read")
        args = json.dumps({"inbox_id": 99999}, ensure_ascii=False)
        result = await inbox_read.on_invoke_tool(
            _make_tool_ctx(inbox_read.name, args), args
        )
        assert result.success is True
        assert result.item is None
        await ext.destroy()
