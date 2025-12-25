from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DialogInfo:
    id: int
    title: str


class TelegramClientManager:
    """
    Thin wrapper around Telethon client.

    This app intentionally does NOT try to do interactive login in the web UI.
    Run scripts/telegram_login.py once on the server to create ./data/telethon.session.
    """

    def __init__(self, *, api_id: int, api_hash: str, session_name: str):
        self._client = TelegramClient(session_name, api_id, api_hash)
        self._authorized: bool = False

    @property
    def client(self) -> TelegramClient:
        return self._client

    @property
    def is_authorized(self) -> bool:
        return self._authorized

    async def start(self) -> None:
        await self._client.connect()
        self._authorized = await self._client.is_user_authorized()
        if not self._authorized:
            logger.warning(
                "Telegram session is NOT authorized yet. Run scripts/telegram_login.py to log in "
                "and create ./data/telethon.session."
            )
            return

        me = await self._client.get_me()
        logger.info("Telegram authorized as %s", _user_display(me))

    async def stop(self) -> None:
        await self._client.disconnect()

    async def list_dialogs(self, *, limit: int = 1000) -> list[DialogInfo]:
        self._ensure_authorized()
        dialogs = await self._client.get_dialogs(limit=limit)
        out: list[DialogInfo] = []
        for d in dialogs:
            title = getattr(d, "name", None) or getattr(d, "title", None) or str(d.id)
            out.append(DialogInfo(id=int(d.id), title=str(title)))
        return out

    async def fetch_last_messages(self, chat_id: int, *, limit: int) -> list[Any]:
        """
        Returns Telethon Message objects, ordered oldest -> newest.
        """

        self._ensure_authorized()
        entity = await self._client.get_input_entity(chat_id)
        messages = await self._client.get_messages(entity, limit=limit)
        # Telethon returns newest->oldest by default.
        return list(reversed(list(messages)))

    async def send_message(self, chat_id: int, text: str) -> Any:
        self._ensure_authorized()
        entity = await self._client.get_input_entity(chat_id)
        try:
            return await self._client.send_message(entity, text)
        except FloodWaitError as e:
            logger.warning("Telegram FloodWaitError: wait %ss", e.seconds)
            raise

    def _ensure_authorized(self) -> None:
        if not self._authorized:
            raise RuntimeError(
                "Telegram is not authorized. Run scripts/telegram_login.py to create a session."
            )


def _user_display(me: User | None) -> str:
    if me is None:
        return "<unknown>"
    username = getattr(me, "username", None)
    if username:
        return f"@{username}"
    first = getattr(me, "first_name", "") or ""
    last = getattr(me, "last_name", "") or ""
    name = (first + " " + last).strip()
    return name or "<user>"


