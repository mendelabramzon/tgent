from __future__ import annotations

import base64
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from app.config import get_settings
from app.db import connect, init_db
from app.logging_config import configure_logging
from app.openai_client import OpenAIClient
from app.prompts import PromptStore
from app.routes import chats_router, prompts_router, settings_router, suggestions_router
from app.scheduler import SuggestionScheduler
from app.telegram_client import TelegramClientManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)

    settings.data_dir.mkdir(parents=True, exist_ok=True)

    conn = await connect(settings.db_path)
    await init_db(conn)

    prompt_store = PromptStore(settings.prompts_dir)
    try:
        prompt_store.reload()
    except Exception:
        logger.exception("Failed to load prompts. Ensure ./prompts/*.json exists.")
        raise

    tg = TelegramClientManager(
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash.get_secret_value(),
        session_name=settings.telegram_session_name,
    )
    await tg.start()

    openai_client = OpenAIClient(
        api_key=(settings.openai_api_key.get_secret_value() if settings.openai_api_key else None),
        model=settings.openai_model,
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )

    openai_summary_client = OpenAIClient(
        api_key=(settings.openai_api_key.get_secret_value() if settings.openai_api_key else None),
        model=settings.openai_summary_model,
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )

    scheduler = SuggestionScheduler(
        conn=conn,
        tg=tg,
        openai_summary=openai_summary_client,
        openai_reply=openai_client,
        prompts=prompt_store,
    )
    await scheduler.start()

    app.state.settings = settings
    app.state.db = conn
    app.state.prompt_store = prompt_store
    app.state.tg = tg
    app.state.openai = openai_client
    app.state.openai_summary = openai_summary_client
    app.state.scheduler = scheduler

    yield

    await scheduler.stop()
    await tg.stop()
    await conn.close()


app = FastAPI(title="Telegram Dashboard Agent", lifespan=lifespan)

def _basic_auth_unauthorized() -> Response:
    return Response(
        content="Unauthorized",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Telegram Dashboard Agent"'},
    )


@app.middleware("http")
async def basic_auth_middleware(request, call_next):  # type: ignore[no-untyped-def]
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        return await call_next(request)

    username = getattr(settings, "dashboard_username", None)
    password_secret = getattr(settings, "dashboard_password", None)
    if not username or password_secret is None:
        return await call_next(request)

    password = password_secret.get_secret_value()
    if not password:
        return await call_next(request)

    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("basic "):
        return _basic_auth_unauthorized()

    encoded = auth.split(" ", 1)[1].strip()
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except Exception:
        return _basic_auth_unauthorized()

    if ":" not in decoded:
        return _basic_auth_unauthorized()
    req_user, req_pass = decoded.split(":", 1)

    if not secrets.compare_digest(req_user, username) or not secrets.compare_digest(req_pass, password):
        return _basic_auth_unauthorized()

    return await call_next(request)

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=(BASE_DIR / "static").as_posix()), name="static")

app.include_router(suggestions_router)
app.include_router(chats_router)
app.include_router(settings_router)
app.include_router(prompts_router)


