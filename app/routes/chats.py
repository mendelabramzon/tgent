from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.services.chats_service import list_chats, set_selected_chats, sync_chats_from_telegram
from app.web import templates

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/chats")
async def chats_page(request: Request) -> object:
    conn = request.app.state.db
    tg = request.app.state.tg
    settings = request.app.state.settings
    prompt_store = request.app.state.prompt_store
    openai = request.app.state.openai

    chats = await list_chats(conn)

    return templates.TemplateResponse(
        "chats.html",
        {
            "request": request,
            "chats": chats,
            "telegram_authorized": tg.is_authorized,
            "openai_configured": openai.enabled,
            "dialogs_limit": settings.telegram_dialogs_limit,
            "prompts_loaded": prompt_store.list(),
        },
    )


@router.post("/chats/sync")
async def chats_sync(request: Request) -> RedirectResponse:
    conn = request.app.state.db
    tg = request.app.state.tg
    settings = request.app.state.settings

    if not tg.is_authorized:
        logger.warning("Chat sync requested but Telegram is not authorized")
        return RedirectResponse(url="/chats", status_code=303)

    await sync_chats_from_telegram(conn, tg, limit=settings.telegram_dialogs_limit)
    return RedirectResponse(url="/chats", status_code=303)


@router.post("/chats/save")
async def chats_save(
    request: Request,
    selected_chat_ids: list[int] = Form(default=[]),
) -> RedirectResponse:
    conn = request.app.state.db
    await set_selected_chats(conn, selected_chat_ids)
    return RedirectResponse(url="/chats", status_code=303)


