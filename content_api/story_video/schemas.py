from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tools.news.fetch_tweets import normalize_tweet_url


class CreateVideo(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, allow_inf_nan=False
    )

    x_url: str = Field(min_length=1, max_length=2048)
    speech_speed: float = Field(default=1.3, ge=0.5, le=2.0)
    instructions: str | None = Field(default=None, max_length=4000)
    send_telegram_preview: bool = True
    telegram_reply_to_message_id: int | None = Field(default=None, ge=1)
    youtube_privacy: Literal["public", "unlisted", "private"] = "public"

    @field_validator("x_url")
    @classmethod
    def canonical_status_url(cls, value: str) -> str:
        return normalize_tweet_url(value)


class JobView(BaseModel):
    id: UUID
    status: Literal["queued", "running", "completed", "failed"]
    stage: str
    created_at: datetime
    updated_at: datetime
    completed_segments: int
    total_segments: int
    error_code: str | None = None
    status_url: str
    video_url: str | None = None
    manifest_url: str | None = None
    duration_seconds: float | None = None
    title: str | None = None
    thumbnail_url: str | None = None
    preview_url: str | None = None
    review_status: str = "not_requested"
    review_error: str | None = None
    youtube_id: str | None = None
    youtube_url: str | None = None


class TelegramDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "cancel"]
    token: str = Field(pattern=r"^[a-f0-9]{12}$")
    chat_id: str = Field(max_length=40)
    message_id: int = Field(ge=1)
    sender_id: str = Field(max_length=40)
