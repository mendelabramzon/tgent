from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    App settings loaded from environment variables / .env.

    Secrets are represented as SecretStr and should never be logged.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram (Telethon user session)
    telegram_api_id: int
    telegram_api_hash: SecretStr
    telegram_phone: str | None = None

    # Telethon uses a "session name" and writes `${name}.session`.
    # Default stores session at ./data/telethon.session
    telegram_session_name: str = "data/telethon"

    # OpenAI
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 30.0
    openai_max_retries: int = 2

    # App
    log_level: str = "INFO"
    data_dir: Path = Path("data")
    db_path: Path = Path("data/app.db")
    prompts_dir: Path = Path("prompts")

    # Telegram sync / UI
    telegram_dialogs_limit: int = 1000


def get_settings() -> Settings:
    return Settings()


