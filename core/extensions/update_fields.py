"""Shared typed sentinel for partial update operations."""

from enum import Enum


class UnsetType(Enum):
    """Sentinel used to distinguish omitted fields from explicit None."""

    TOKEN = "UNSET"


UNSET = UnsetType.TOKEN
