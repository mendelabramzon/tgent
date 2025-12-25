from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import aiosqlite

logger = logging.getLogger(__name__)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


async def connect(db_path: Path) -> aiosqlite.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path.as_posix())
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON;")
    await conn.execute("PRAGMA journal_mode = WAL;")
    await conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


async def init_db(conn: aiosqlite.Connection) -> None:
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            language_hint TEXT NULL,
            is_selected INTEGER NOT NULL DEFAULT 0,
            last_seen_message_id INTEGER NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_chats_selected ON chats(is_selected);

        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            source_messages_json TEXT NOT NULL,
            suggested_text TEXT NOT NULL,
            ru_translation TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_suggestions_status_created
            ON suggestions(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_suggestions_chat_status
            ON suggestions(chat_id, status);

        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            k_messages INTEGER NOT NULL,
            n_minutes INTEGER NOT NULL,
            max_suggestions_per_chat INTEGER NOT NULL,
            cooldown_minutes INTEGER NULL,
            updated_at TEXT NOT NULL
        );
        """
    )

    # Ensure a singleton settings row exists.
    now = utcnow_iso()
    await conn.execute(
        """
        INSERT INTO settings (id, k_messages, n_minutes, max_suggestions_per_chat, cooldown_minutes, updated_at)
        VALUES (1, 20, 5, 1, 0, ?)
        ON CONFLICT(id) DO NOTHING;
        """,
        (now,),
    )
    await conn.commit()

    logger.info("SQLite initialized at %s", _db_path_for_log(conn))


def _db_path_for_log(conn: aiosqlite.Connection) -> str:
    # aiosqlite does not expose a nice path, but conn._conn is sqlite3.Connection.
    try:
        return str(conn._conn)  # type: ignore[attr-defined]
    except Exception:
        return "<sqlite>"


async def fetch_one(
    conn: aiosqlite.Connection, sql: str, params: Sequence[Any] | None = None
) -> aiosqlite.Row | None:
    async with conn.execute(sql, params or ()) as cur:
        return await cur.fetchone()


async def fetch_all(
    conn: aiosqlite.Connection, sql: str, params: Sequence[Any] | None = None
) -> list[aiosqlite.Row]:
    async with conn.execute(sql, params or ()) as cur:
        return await cur.fetchall()


