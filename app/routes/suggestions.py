from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from app.models import SuggestionStatus
from app.services.chats_service import update_chat_last_seen_message_id
from app.services.suggestions_service import get_suggestion, list_suggestions, update_suggestion_status
from app.web import templates

logger = logging.getLogger(__name__)

router = APIRouter()


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


