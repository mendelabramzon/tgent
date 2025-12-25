from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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

    scheduler = SuggestionScheduler(conn=conn, tg=tg, openai=openai_client, prompts=prompt_store)
    await scheduler.start()

    app.state.settings = settings
    app.state.db = conn
    app.state.prompt_store = prompt_store
    app.state.tg = tg
    app.state.openai = openai_client
    app.state.scheduler = scheduler

    yield

    await scheduler.stop()
    await tg.stop()
    await conn.close()


app = FastAPI(title="Telegram Dashboard Agent", lifespan=lifespan)

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=(BASE_DIR / "static").as_posix()), name="static")

app.include_router(suggestions_router)
app.include_router(chats_router)
app.include_router(settings_router)
app.include_router(prompts_router)


