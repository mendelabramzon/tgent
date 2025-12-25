from __future__ import annotations

import json
import logging
import re
import shutil

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_PROMPT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


@router.post("/prompts/reload")
async def reload_prompts(request: Request) -> RedirectResponse:
    prompt_store = request.app.state.prompt_store
    try:
        prompt_store.reload()
    except Exception:
        logger.exception("Failed to reload prompts")
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/prompts/update")
async def update_prompt(
    request: Request,
    name: str = Form(...),
    role: str = Form(...),
    content: str = Form(...),
) -> RedirectResponse:
    """
    Update a prompt JSON on disk and hot-reload into memory.

    This intentionally only supports editing by prompt name (filename stem).
    """

    name = (name or "").strip()
    role = (role or "").strip()
    content = (content or "").rstrip() + "\n"

    if not _PROMPT_NAME_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail="Invalid prompt name.")
    if role not in {"system", "user"}:
        raise HTTPException(status_code=400, detail="Invalid role (expected system/user).")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Prompt content cannot be empty.")

    settings = request.app.state.settings
    prompts_dir = settings.prompts_dir.resolve()
    path = (prompts_dir / f"{name}.json").resolve()
    if path.parent != prompts_dir:
        raise HTTPException(status_code=400, detail="Invalid prompt path.")

    payload = {"role": role, "content": content}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    prompt_store = request.app.state.prompt_store
    try:
        prompt_store.reload()
    except Exception:
        logger.exception("Prompt saved, but reload failed")

    # Optional: wake scheduler so a new cycle can pick up updated prompts sooner.
    try:
        request.app.state.scheduler.wake()
    except Exception:
        pass

    return RedirectResponse(url="/settings", status_code=303)


@router.post("/prompts/restore-defaults")
async def restore_default_prompts(request: Request) -> RedirectResponse:
    """
    Restore default prompts by overwriting ./prompts/*.json with ./prompts_defaults/*.json.

    This is a "factory reset" for shipped prompts and is intentionally coarse-grained.
    """

    settings = request.app.state.settings
    prompts_dir = settings.prompts_dir.resolve()
    defaults_dir = settings.prompts_defaults_dir.resolve()

    try:
        if not defaults_dir.exists():
            raise FileNotFoundError(f"Defaults dir not found: {defaults_dir}")

        prompts_dir.mkdir(parents=True, exist_ok=True)

        restored = 0
        for src in sorted(defaults_dir.glob("*.json")):
            dst = (prompts_dir / src.name).resolve()
            if dst.parent != prompts_dir:
                continue
            shutil.copyfile(src, dst)
            restored += 1

        request.app.state.prompt_store.reload()
        try:
            request.app.state.scheduler.wake()
        except Exception:
            pass

        logger.info("Restored %s default prompt files from %s", restored, defaults_dir)
    except Exception:
        logger.exception("Failed to restore default prompts")

    return RedirectResponse(url="/settings", status_code=303)


