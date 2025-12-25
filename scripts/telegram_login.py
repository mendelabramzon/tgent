from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from telethon import TelegramClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if PROJECT_ROOT.as_posix() not in sys.path:
    sys.path.insert(0, PROJECT_ROOT.as_posix())

from app.config import get_settings
from app.logging_config import configure_logging


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    phone = settings.telegram_phone
    if not phone:
        phone = input("Telegram phone (international format, e.g. +15551234567): ").strip()

    client = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash.get_secret_value(),
    )

    # This will prompt for the login code (and 2FA password if enabled).
    await client.start(phone=phone)

    me = await client.get_me()
    who = getattr(me, "username", None) or getattr(me, "first_name", None) or "<user>"
    print(f"Logged in as: {who}")
    print(f"Session saved. You should now have: {settings.telegram_session_name}.session")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())


