from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from app.models import SuggestionStatus
from app.services.chats_service import update_chat_last_seen_message_id
from app.services.suggestions_service import get_suggestion, list_suggestions, update_suggestion_status
from app.web import templates

logger = logging.getLogger(__name__)

router = APIRouter()

_QUESTION_RE = re.compile(r"[?？¿]")


def _parse_status(value: str | None) -> SuggestionStatus | None:
    if not value:
        return None
    try:
        return SuggestionStatus(value)
    except Exception:
        return None


@router.get("/")
async def suggestions_page(request: Request, status: str | None = Query(default=None)) -> object:
    conn = request.app.state.db
    tg = request.app.state.tg
    openai = request.app.state.openai
    prompt_store = request.app.state.prompt_store

    parsed = _parse_status(status)
    suggestions = await list_suggestions(conn, status=parsed, limit=200)

    return templates.TemplateResponse(
        "suggestions.html",
        {
            "request": request,
            "suggestions": suggestions,
            "status_filter": parsed.value if parsed else "",
            "telegram_authorized": tg.is_authorized,
            "openai_configured": openai.enabled,
            "prompts_loaded": prompt_store.list(),
        },
    )


@router.post("/run-now")
async def run_now(request: Request) -> RedirectResponse:
    scheduler = request.app.state.scheduler
    # Fire-and-forget. Scheduler has an internal lock to avoid overlapping cycles.
    asyncio.create_task(scheduler.run_once())
    return RedirectResponse(url="/", status_code=303)


@router.post("/suggestions/{suggestion_id}/decline")
async def decline_suggestion(request: Request, suggestion_id: int) -> RedirectResponse:
    conn = request.app.state.db
    await update_suggestion_status(conn, suggestion_id=suggestion_id, status=SuggestionStatus.declined)
    return RedirectResponse(url="/", status_code=303)


@router.post("/suggestions/{suggestion_id}/send")
async def send_suggestion(request: Request, suggestion_id: int) -> RedirectResponse:
    conn = request.app.state.db
    tg = request.app.state.tg

    row = await get_suggestion(conn, suggestion_id)
    if row is None:
        return RedirectResponse(url="/", status_code=303)

    if row["status"] != SuggestionStatus.pending.value:
        return RedirectResponse(url="/", status_code=303)

    chat_id = int(row["chat_id"])
    text = str(row["suggested_text"] or "").strip()
    if not text:
        await update_suggestion_status(
            conn,
            suggestion_id=suggestion_id,
            status=SuggestionStatus.failed,
            error="Empty suggested_text; nothing to send.",
        )
        return RedirectResponse(url="/?status=failed", status_code=303)

    try:
        msg = await tg.send_message(chat_id, text)
        msg_id = int(getattr(msg, "id", 0) or 0)
        if msg_id:
            await update_chat_last_seen_message_id(conn, chat_id=chat_id, last_seen_message_id=msg_id)
        await update_suggestion_status(conn, suggestion_id=suggestion_id, status=SuggestionStatus.sent)
    except Exception as e:
        logger.exception("Failed to send suggestion_id=%s chat_id=%s", suggestion_id, chat_id)
        await update_suggestion_status(
            conn,
            suggestion_id=suggestion_id,
            status=SuggestionStatus.failed,
            error=str(e),
        )
        return RedirectResponse(url="/?status=failed", status_code=303)

    return RedirectResponse(url="/?status=sent", status_code=303)


@router.post("/suggestions/{suggestion_id}/send-reply")
async def send_suggestion_as_reply(request: Request, suggestion_id: int) -> RedirectResponse:
    conn = request.app.state.db
    tg = request.app.state.tg

    row = await get_suggestion(conn, suggestion_id)
    if row is None:
        return RedirectResponse(url="/", status_code=303)
    if row["status"] != SuggestionStatus.pending.value:
        return RedirectResponse(url="/", status_code=303)

    chat_id = int(row["chat_id"])
    text = str(row["suggested_text"] or "").strip()
    if not text:
        await update_suggestion_status(
            conn,
            suggestion_id=suggestion_id,
            status=SuggestionStatus.failed,
            error="Empty suggested_text; nothing to send.",
        )
        return RedirectResponse(url="/?status=failed", status_code=303)

    # Prefer the model-selected reply target (best fit), validated/stored at generation time.
    reply_to_id: int | None = None
    try:
        mid = row.get("reply_to_message_id")
        if isinstance(mid, int) and mid > 0:
            reply_to_id = mid
    except Exception:
        reply_to_id = None

    # Fallback for older suggestions: pick a "best fit" incoming message from source_messages_json.
    if reply_to_id is None:
        try:
            src_raw = str(row.get("source_messages_json") or "[]")
            src = json.loads(src_raw)
            best_score = -10_000
            best_id: int | None = None
            if isinstance(src, list):
                for idx, m in enumerate(src):
                    if not isinstance(m, dict):
                        continue
                    if m.get("from_me") is not False:
                        continue
                    mid = m.get("id")
                    text_m = str(m.get("text") or "").strip()
                    if not isinstance(mid, int) or mid <= 0:
                        continue

                    # Heuristic scoring: recency + question-ness + substance.
                    score = idx  # more recent messages have higher idx
                    if _QUESTION_RE.search(text_m):
                        score += 50
                    if len(text_m) < 3:
                        score -= 25
                    if not re.search(r"\w", text_m, flags=re.UNICODE):
                        score -= 25

                    if score > best_score:
                        best_score = score
                        best_id = mid

            reply_to_id = best_id
        except Exception:
            reply_to_id = None

    if reply_to_id is None:
        # Fallback: just send as normal message.
        return await send_suggestion(request, suggestion_id)

    try:
        msg = await tg.send_message(chat_id, text, reply_to_message_id=reply_to_id)
        msg_id = int(getattr(msg, "id", 0) or 0)
        if msg_id:
            await update_chat_last_seen_message_id(conn, chat_id=chat_id, last_seen_message_id=msg_id)
        await update_suggestion_status(conn, suggestion_id=suggestion_id, status=SuggestionStatus.sent)
    except Exception as e:
        logger.exception(
            "Failed to send suggestion_id=%s as reply chat_id=%s reply_to=%s",
            suggestion_id,
            chat_id,
            reply_to_id,
        )
        await update_suggestion_status(
            conn,
            suggestion_id=suggestion_id,
            status=SuggestionStatus.failed,
            error=str(e),
        )
        return RedirectResponse(url="/?status=failed", status_code=303)

    return RedirectResponse(url="/?status=sent", status_code=303)


