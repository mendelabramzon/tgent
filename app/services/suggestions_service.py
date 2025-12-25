from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import aiosqlite

from app.db import fetch_all, fetch_one, utcnow_iso
from app.models import ReplySuggestion, SettingsRecord, SourceMessage, SuggestionStatus, SuggestionView
from app.openai_client import OpenAIClient
from app.prompts import PromptStore
from app.services.chats_service import get_selected_chats, update_chat_last_seen_message_id
from app.telegram_client import TelegramClientManager

logger = logging.getLogger(__name__)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


async def get_settings(conn: aiosqlite.Connection) -> SettingsRecord:
    row = await fetch_one(
        conn,
        """
        SELECT k_messages, n_minutes, max_suggestions_per_chat, cooldown_minutes
        FROM settings
        WHERE id = 1;
        """
    )
    if row is None:
        # Should not happen due to init_db, but be defensive.
        return SettingsRecord()
    return SettingsRecord(
        k_messages=int(row["k_messages"]),
        n_minutes=int(row["n_minutes"]),
        max_suggestions_per_chat=int(row["max_suggestions_per_chat"]),
        cooldown_minutes=int(row["cooldown_minutes"] or 0),
    )


async def save_settings(conn: aiosqlite.Connection, settings: SettingsRecord) -> None:
    now = utcnow_iso()
    await conn.execute(
        """
        UPDATE settings
        SET k_messages = ?,
            n_minutes = ?,
            max_suggestions_per_chat = ?,
            cooldown_minutes = ?,
            updated_at = ?
        WHERE id = 1;
        """,
        (
            settings.k_messages,
            settings.n_minutes,
            settings.max_suggestions_per_chat,
            int(settings.cooldown_minutes or 0),
            now,
        ),
    )
    await conn.commit()


async def list_suggestions(
    conn: aiosqlite.Connection,
    *,
    status: SuggestionStatus | None = None,
    limit: int = 200,
) -> list[SuggestionView]:
    params: list[Any] = []
    where = ""
    if status is not None:
        where = "WHERE s.status = ?"
        params.append(status.value)

    params.append(int(limit))

    rows = await fetch_all(
        conn,
        f"""
        SELECT
            s.id,
            s.chat_id,
            c.title AS chat_title,
            s.created_at,
            s.suggested_text,
            s.ru_translation,
            s.status,
            s.error
        FROM suggestions s
        JOIN chats c ON c.id = s.chat_id
        {where}
        ORDER BY
            CASE s.status
                WHEN 'pending' THEN 0
                WHEN 'failed' THEN 1
                WHEN 'sent' THEN 2
                WHEN 'declined' THEN 3
                ELSE 9
            END,
            s.created_at DESC
        LIMIT ?;
        """,
        params,
    )

    out: list[SuggestionView] = []
    for r in rows:
        out.append(
            SuggestionView(
                id=int(r["id"]),
                chat_id=int(r["chat_id"]),
                chat_title=str(r["chat_title"]),
                created_at=_parse_dt(r["created_at"]) or datetime.now(timezone.utc),
                suggested_text=str(r["suggested_text"] or ""),
                ru_translation=str(r["ru_translation"] or ""),
                status=SuggestionStatus(str(r["status"])),
                error=r["error"],
            )
        )
    return out


async def get_suggestion(conn: aiosqlite.Connection, suggestion_id: int) -> dict[str, Any] | None:
    row = await fetch_one(
        conn,
        """
        SELECT id, chat_id, created_at, source_messages_json, suggested_text, ru_translation, status, error, updated_at
        FROM suggestions
        WHERE id = ?;
        """,
        (int(suggestion_id),),
    )
    if row is None:
        return None
    return dict(row)


async def update_suggestion_status(
    conn: aiosqlite.Connection,
    *,
    suggestion_id: int,
    status: SuggestionStatus,
    error: str | None = None,
) -> None:
    now = utcnow_iso()
    await conn.execute(
        "UPDATE suggestions SET status = ?, error = ?, updated_at = ? WHERE id = ?;",
        (status.value, error, now, int(suggestion_id)),
    )
    await conn.commit()


async def create_suggestion(
    conn: aiosqlite.Connection,
    *,
    chat_id: int,
    source_messages_json: str,
    suggested_text: str,
    ru_translation: str,
    status: SuggestionStatus,
    error: str | None = None,
) -> int:
    now = utcnow_iso()
    cur = await conn.execute(
        """
        INSERT INTO suggestions
            (chat_id, created_at, source_messages_json, suggested_text, ru_translation, status, error, updated_at)
        VALUES
            (?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            int(chat_id),
            now,
            source_messages_json,
            suggested_text,
            ru_translation,
            status.value,
            error,
            now,
        ),
    )
    await conn.commit()
    return int(cur.lastrowid)


async def _count_pending_suggestions(conn: aiosqlite.Connection, chat_id: int) -> int:
    row = await fetch_one(
        conn,
        "SELECT COUNT(*) AS cnt FROM suggestions WHERE chat_id = ? AND status = 'pending';",
        (int(chat_id),),
    )
    return int(row["cnt"] if row else 0)


async def _latest_suggestion_created_at(conn: aiosqlite.Connection, chat_id: int) -> datetime | None:
    row = await fetch_one(
        conn,
        "SELECT created_at FROM suggestions WHERE chat_id = ? ORDER BY created_at DESC LIMIT 1;",
        (int(chat_id),),
    )
    return _parse_dt(row["created_at"] if row else None)


def _message_date_iso(message: Any) -> str:
    dt = getattr(message, "date", None)
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.replace(microsecond=0).isoformat()
    return utcnow_iso()


def _message_text(message: Any) -> str:
    # Telethon stores text in .message
    text = getattr(message, "message", "") or ""
    return str(text).strip()


def _message_id(message: Any) -> int:
    return int(getattr(message, "id", 0) or 0)


def _message_from_me(message: Any) -> bool:
    return bool(getattr(message, "out", False))


async def generate_suggestions_cycle(
    conn: aiosqlite.Connection,
    *,
    tg: TelegramClientManager,
    openai: OpenAIClient,
    prompts: PromptStore,
) -> None:
    """
    Periodic job:
    - for each selected chat: fetch last K messages
    - generate a reply suggestion (+ RU translation)
    - store Suggestion rows as pending/failed
    """

    if not tg.is_authorized:
        logger.debug("Skipping suggestion cycle: Telegram not authorized")
        return

    if not openai.enabled:
        logger.debug("Skipping suggestion cycle: OpenAI not configured")
        return

    settings = await get_settings(conn)
    chats = await get_selected_chats(conn)
    if not chats:
        logger.debug("Skipping suggestion cycle: no selected chats")
        return

    system_prompt = prompts.get("system").content

    logger.info(
        "Suggestion cycle start: selected_chats=%s k=%s max_pending=%s cooldown=%s min",
        len(chats),
        settings.k_messages,
        settings.max_suggestions_per_chat,
        settings.cooldown_minutes,
    )

    for chat in chats:
        try:
            pending_count = await _count_pending_suggestions(conn, chat.id)
            if pending_count >= settings.max_suggestions_per_chat:
                continue

            cooldown_min = int(settings.cooldown_minutes or 0)
            if cooldown_min > 0:
                last_created = await _latest_suggestion_created_at(conn, chat.id)
                if last_created is not None:
                    now = datetime.now(timezone.utc)
                    # last_created is stored as ISO; may be naive if edited manually.
                    if last_created.tzinfo is None:
                        last_created = last_created.replace(tzinfo=timezone.utc)
                    if now - last_created < timedelta(minutes=cooldown_min):
                        continue

            messages = await tg.fetch_last_messages(chat.id, limit=settings.k_messages)
            if not messages:
                continue

            # Keep only text messages
            source: list[SourceMessage] = []
            for m in messages:
                text = _message_text(m)
                if not text:
                    continue
                source.append(
                    SourceMessage(
                        id=_message_id(m),
                        date_iso=_message_date_iso(m),
                        from_me=_message_from_me(m),
                        sender_name=("me" if _message_from_me(m) else "other"),
                        text=text,
                    )
                )

            if not source:
                continue

            latest_id = max((m.id for m in source), default=0)
            if chat.last_seen_message_id is not None and latest_id <= chat.last_seen_message_id:
                continue

            # If the most recent message is ours, usually no reply is needed.
            if source and source[-1].from_me:
                await update_chat_last_seen_message_id(conn, chat_id=chat.id, last_seen_message_id=latest_id)
                continue

            messages_json = json.dumps([m.model_dump() for m in source], ensure_ascii=False)

            user_prompt = prompts.render(
                "suggest_reply",
                chat_title=chat.title,
                language_hint=(chat.language_hint or ""),
                messages_json=messages_json,
            ).content

            reply: ReplySuggestion = await openai.suggest_reply(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

            await create_suggestion(
                conn,
                chat_id=chat.id,
                source_messages_json=messages_json,
                suggested_text=reply.suggested_text,
                ru_translation=reply.ru_translation,
                status=SuggestionStatus.pending,
            )
            await update_chat_last_seen_message_id(conn, chat_id=chat.id, last_seen_message_id=latest_id)

        except Exception as e:
            logger.exception("Suggestion generation failed for chat_id=%s (%s)", chat.id, chat.title)
            # Try to persist a failed suggestion for visibility (but don't crash the whole cycle).
            try:
                await create_suggestion(
                    conn,
                    chat_id=chat.id,
                    source_messages_json="[]",
                    suggested_text="",
                    ru_translation="",
                    status=SuggestionStatus.failed,
                    error=str(e),
                )
            except Exception:
                logger.exception("Failed to persist failed suggestion record for chat_id=%s", chat.id)

    logger.info("Suggestion cycle done")


async def cleanup_old_suggestions(
    conn: aiosqlite.Connection, *, keep_last_per_chat: int = 200
) -> None:
    """
    Optional helper if you ever want to prevent unbounded DB growth.
    Not called by default.
    """

    if keep_last_per_chat <= 0:
        return

    # Keep it simple: delete suggestions beyond N per chat (by created_at desc).
    # Note: This is a best-effort cleanup; SQLite window functions could do this more neatly.
    rows = await fetch_all(conn, "SELECT DISTINCT chat_id FROM suggestions;")
    for r in rows:
        chat_id = int(r["chat_id"])
        ids = await fetch_all(
            conn,
            """
            SELECT id FROM suggestions
            WHERE chat_id = ?
            ORDER BY created_at DESC
            LIMIT -1 OFFSET ?;
            """,
            (chat_id, keep_last_per_chat),
        )
        if ids:
            await conn.executemany("DELETE FROM suggestions WHERE id = ?;", [(int(x["id"]),) for x in ids])
    await conn.commit()


