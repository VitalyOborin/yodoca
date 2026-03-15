"""SQLite-backed session with Unicode stored as-is (no escaping)."""

import asyncio
import json
import threading

from agents import SQLiteSession
from agents.items import TResponseInputItem


class UnicodeSQLiteSession(SQLiteSession):
    """SQLiteSession that stores message_data without Unicode escaping.

    Uses json.dumps(..., ensure_ascii=False) per project rule:
    Unicode/UTF-8 must be saved "as is", without escaping.
    """

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        """Add new items to the conversation history.

        Overrides parent to use ensure_ascii=False for message_data.
        """
        if not items:
            return

        def _add_items_sync() -> None:
            conn = self._get_connection()

            with self._lock if self._is_memory_db else threading.Lock():
                conn.execute(
                    f"""
                    INSERT OR IGNORE INTO {self.sessions_table} (session_id) VALUES (?)
                """,
                    (self.session_id,),
                )

                message_data = [
                    (self.session_id, json.dumps(item, ensure_ascii=False))
                    for item in items
                ]
                conn.executemany(
                    f"""
                    INSERT INTO {self.messages_table} (session_id, message_data)
                    VALUES (?, ?)
                """,
                    message_data,
                )

                conn.execute(
                    f"""
                    UPDATE {self.sessions_table}
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE session_id = ?
                """,
                    (self.session_id,),
                )

                conn.commit()

        await asyncio.to_thread(_add_items_sync)
