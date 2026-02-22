"""Shared search filter for memory queries. Eliminates duplicated filter logic."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SearchFilter:
    """Filter parameters for memory search (kind, tag, time range, session exclusion)."""

    kind: str | None = None
    tag: str | None = None
    after_ts: int | None = None
    before_ts: int | None = None
    exclude_session_id: str | None = None

    def build_clauses(self, alias: str = "m") -> tuple[str, list[Any]]:
        """Return (sql_where_fragment, params) for appending to WHERE clause."""
        clauses: list[str] = []
        params: list[Any] = []
        if self.kind is not None:
            clauses.append(f" AND {alias}.kind = ?")
            params.append(self.kind)
        if self.tag is not None:
            clauses.append(f" AND {alias}.tags LIKE ?")
            params.append(f'%"{self.tag}"%')
        if self.after_ts is not None:
            clauses.append(f" AND {alias}.created_at >= ?")
            params.append(self.after_ts)
        if self.before_ts is not None:
            clauses.append(f" AND {alias}.created_at <= ?")
            params.append(self.before_ts)
        if self.exclude_session_id is not None:
            clauses.append(
                f" AND ({alias}.session_id IS NULL OR {alias}.session_id != ?)"
            )
            params.append(self.exclude_session_id)
        return "".join(clauses), params
