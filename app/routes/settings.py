from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.models import SettingsRecord
from app.services.suggestions_service import get_settings, save_settings
from app.web import templates

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/settings")
async def settings_page(request: Request) -> object:
    conn = request.app.state.db
    tg = request.app.state.tg
    openai = request.app.state.openai
    prompt_store = request.app.state.prompt_store

    settings = await get_settings(conn)
    prompts = [prompt_store.get(name) for name in prompt_store.list()]
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "prompts": prompts,
            "telegram_authorized": tg.is_authorized,
            "openai_configured": openai.enabled,
            "prompts_loaded": prompt_store.list(),
        },
    )


@router.post("/settings/save")
async def settings_save(
    request: Request,
    k_messages: int = Form(...),
    n_minutes: int = Form(...),
    max_suggestions_per_chat: int = Form(...),
    cooldown_minutes: int = Form(default=0),
) -> RedirectResponse:
    conn = request.app.state.db
    scheduler = request.app.state.scheduler

    settings = SettingsRecord(
        k_messages=int(k_messages),
        n_minutes=int(n_minutes),
        max_suggestions_per_chat=int(max_suggestions_per_chat),
        cooldown_minutes=int(cooldown_minutes),
    )
    await save_settings(conn, settings)
    scheduler.wake()
    return RedirectResponse(url="/settings", status_code=303)


