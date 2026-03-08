"""Pydantic models for Inbox extension API and tools."""

from typing import Literal

from pydantic import BaseModel, Field


class InboxItemInput(BaseModel):
    """Input for upsert_item. payload_hash is computed internally."""

    source_type: str
    source_account: str
    entity_type: str
    external_id: str
    title: str = ""
    occurred_at: float
    status: Literal["active", "deleted"] = "active"
    payload: dict = Field(default_factory=dict)


class InboxItem(BaseModel):
    """Full envelope row returned by get_item and list_items."""

    id: int
    source_type: str
    source_account: str
    entity_type: str
    external_id: str
    title: str = ""
    occurred_at: float
    ingested_at: float
    status: str = "active"
    is_current: bool = True
    payload: dict = Field(default_factory=dict)
    payload_hash: str = ""


class InboxWriteResult(BaseModel):
    """Result of upsert_item (service API)."""

    success: bool
    inbox_id: int = 0
    change_type: Literal["created", "updated", "deleted", "duplicate"] = "duplicate"
    error: str | None = None


class InboxListResult(BaseModel):
    """Result of inbox_list tool."""

    success: bool
    items: list[InboxItem] = Field(default_factory=list)
    total: int = 0
    error: str | None = None


class InboxReadResult(BaseModel):
    """Result of inbox_read tool."""

    success: bool
    item: InboxItem | None = None
    error: str | None = None
