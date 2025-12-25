from __future__ import annotations

import asyncio
import logging

import aiosqlite

from app.openai_client import OpenAIClient
from app.prompts import PromptStore
from app.services.suggestions_service import generate_suggestions_cycle, get_settings
from app.telegram_client import TelegramClientManager

logger = logging.getLogger(__name__)


class SuggestionScheduler:
    """
    Simple asyncio-based scheduler started from FastAPI lifespan.

    It re-reads Settings from SQLite each loop, so changing N minutes in the UI takes effect
    without restarting the server.
    """

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection,
        tg: TelegramClientManager,
        openai: OpenAIClient,
        prompts: PromptStore,
    ):
        self._conn = conn
        self._tg = tg
        self._openai = openai
        self._prompts = prompts

        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wakeup = asyncio.Event()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._wakeup.set()  # run quickly after startup
        self._task = asyncio.create_task(self._run_loop(), name="suggestion-scheduler")
        logger.info("Scheduler started")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._wakeup.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
        logger.info("Scheduler stopped")

    def wake(self) -> None:
        self._wakeup.set()

    async def run_once(self) -> None:
        async with self._lock:
            await generate_suggestions_cycle(
                self._conn,
                tg=self._tg,
                openai=self._openai,
                prompts=self._prompts,
            )

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._maybe_wait()
                if self._stop.is_set():
                    break
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Scheduler loop error")

    async def _maybe_wait(self) -> None:
        settings = await get_settings(self._conn)
        seconds = max(30, int(settings.n_minutes) * 60)
        self._wakeup.clear()
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return


