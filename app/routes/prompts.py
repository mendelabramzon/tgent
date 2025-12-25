from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/prompts/reload")
async def reload_prompts(request: Request) -> RedirectResponse:
    prompt_store = request.app.state.prompt_store
    try:
        prompt_store.reload()
    except Exception:
        logger.exception("Failed to reload prompts")
    return RedirectResponse(url="/settings", status_code=303)


