from __future__ import annotations

import logging
from logging.config import dictConfig


def configure_logging(level: str = "INFO") -> None:
    """
    Configure structured-ish stdout logging.

    Keep it simple: timestamps + level + logger + message.
    """

    level = (level or "INFO").upper()

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                }
            },
            "root": {"level": level, "handlers": ["console"]},
            # Keep noisy libs reasonable
            "loggers": {
                "uvicorn.error": {"level": level},
                "uvicorn.access": {"level": level},
                "telethon": {"level": "WARNING"},
                "openai": {"level": "WARNING"},
            },
        }
    )

    logging.getLogger(__name__).debug("Logging configured: level=%s", level)


