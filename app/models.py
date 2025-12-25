from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SuggestionStatus(str, Enum):
    pending = "pending"
    sent = "sent"
    declined = "declined"
    failed = "failed"


class ChatRecord(BaseModel):
    id: int
    title: str
    language_hint: str | None = None
    is_selected: bool = False
    last_seen_message_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SourceMessage(BaseModel):
    id: int
    date_iso: str
    from_me: bool
    sender_name: str | None = None
    text: str


class ReplySuggestion(BaseModel):
    suggested_text: str = Field(min_length=1)
    ru_translation: str = Field(min_length=1)
    # When sending as "reply", reply to this Telegram message id (must exist in provided source messages).
    reply_to_message_id: int | None = None


class SuggestionRecord(BaseModel):
    id: int
    chat_id: int
    created_at: datetime
    source_messages_json: str
    suggested_text: str
    ru_translation: str
    status: SuggestionStatus
    error: str | None = None
    updated_at: datetime


class SuggestionView(BaseModel):
    id: int
    chat_id: int
    chat_title: str
    created_at: datetime
    suggested_text: str
    ru_translation: str
    status: SuggestionStatus
    error: str | None = None


class SettingsRecord(BaseModel):
    k_messages: int = Field(default=20, ge=1, le=100)
    n_minutes: int = Field(default=5, ge=1, le=1440)
    max_suggestions_per_chat: int = Field(default=1, ge=1, le=10)
    cooldown_minutes: int | None = Field(default=0, ge=0, le=1440)


JsonDict = dict[str, Any]


