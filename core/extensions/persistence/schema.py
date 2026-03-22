"""Shared SQLite schema bootstrap for thread and project persistence."""

from pathlib import Path


def ensure_thread_schema(db_path: str) -> None:
    """Create the shared thread/project schema if it does not exist yet."""
    import sqlite3

    def _ensure_column(
        conn: sqlite3.Connection,
        *,
        table: str,
        column: str,
        ddl: str,
    ) -> None:
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                icon TEXT,
                instructions TEXT,
                agent_config TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_files (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                file_path TEXT NOT NULL,
                added_at INTEGER NOT NULL,
                PRIMARY KEY (project_id, file_path)
            );

            CREATE TABLE IF NOT EXISTS project_links (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                url TEXT NOT NULL,
                added_at INTEGER NOT NULL,
                PRIMARY KEY (project_id, url)
            );

            CREATE TABLE IF NOT EXISTS threads (
                thread_id TEXT PRIMARY KEY,
                project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                title TEXT,
                channel_id TEXT NOT NULL DEFAULT 'unknown',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active_at INTEGER NOT NULL DEFAULT 0,
                is_archived INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        _ensure_column(
            conn,
            table="projects",
            column="description",
            ddl="description TEXT",
        )
        _ensure_column(
            conn,
            table="projects",
            column="icon",
            ddl="icon TEXT",
        )
        conn.commit()
    finally:
        conn.close()
