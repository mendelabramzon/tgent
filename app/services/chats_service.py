from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

import aiosqlite

from app.db import fetch_all, utcnow_iso
from app.models import ChatRecord
from app.telegram_client import DialogInfo, TelegramClientManager

logger = logging.getLogger(__name__)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


async def list_chats(conn: aiosqlite.Connection) -> list[ChatRecord]:
    rows = await fetch_all(
        conn,
        """
        SELECT id, title, language_hint, is_selected, last_seen_message_id, created_at, updated_at
        FROM chats
        ORDER BY lower(title) ASC;
        """
    )
    out: list[ChatRecord] = []
    for r in rows:
        out.append(
            ChatRecord(
                id=int(r["id"]),
                title=str(r["title"]),
                language_hint=r["language_hint"],
                is_selected=bool(r["is_selected"]),
                last_seen_message_id=r["last_seen_message_id"],
                created_at=_parse_dt(r["created_at"]),
                updated_at=_parse_dt(r["updated_at"]),
            )
        )
    return out


async def get_selected_chats(conn: aiosqlite.Connection) -> list[ChatRecord]:
    rows = await fetch_all(
        conn,
        """
        SELECT id, title, language_hint, is_selected, last_seen_message_id, created_at, updated_at
        FROM chats
        WHERE is_selected = 1
        ORDER BY lower(title) ASC;
        """
    )
    out: list[ChatRecord] = []
    for r in rows:
        out.append(
            ChatRecord(
                id=int(r["id"]),
                title=str(r["title"]),
                language_hint=r["language_hint"],
                is_selected=True,
                last_seen_message_id=r["last_seen_message_id"],
                created_at=_parse_dt(r["created_at"]),
                updated_at=_parse_dt(r["updated_at"]),
            )
        )
    return out


async def set_selected_chats(conn: aiosqlite.Connection, selected_chat_ids: Iterable[int]) -> None:
    selected = {int(x) for x in selected_chat_ids}
    now = utcnow_iso()

    await conn.execute("UPDATE chats SET is_selected = 0, updated_at = ?;", (now,))
    if selected:
        await conn.executemany(
            "UPDATE chats SET is_selected = 1, updated_at = ? WHERE id = ?;",
            [(now, chat_id) for chat_id in selected],
        )
    await conn.commit()


async def sync_chats_from_telegram(
    conn: aiosqlite.Connection,
    tg: TelegramClientManager,
    *,
    limit: int,
) -> int:
    """
    Fetches dialogs from Telegram and upserts them into `chats`.
    Returns the number of dialogs processed.
    """

    dialogs: list[DialogInfo] = await tg.list_dialogs(limit=limit)
    now = utcnow_iso()
    params = [(d.id, d.title, now, now) for d in dialogs]

    await conn.executemany(
        """
        INSERT INTO chats (id, title, is_selected, created_at, updated_at)
        VALUES (?, ?, 0, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            updated_at = excluded.updated_at;
        """,
        params,
    )
    await conn.commit()

    logger.info("Synced %s chats from Telegram", len(dialogs))
    return len(dialogs)


async def update_chat_last_seen_message_id(
    conn: aiosqlite.Connection, *, chat_id: int, last_seen_message_id: int
) -> None:
    now = utcnow_iso()
    await conn.execute(
        "UPDATE chats SET last_seen_message_id = ?, updated_at = ? WHERE id = ?;",
        (int(last_seen_message_id), now, int(chat_id)),
    )
    await conn.commit()


