"""Outgoing Telegram delivery only; callbacks belong to the existing watcher."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from uuid import UUID

import requests

from .errors import PipelineError
from .review import VideoPackage


@dataclass(frozen=True)
class TelegramSettings:
    token: str = field(repr=False)
    chat_id: str
    allowed_users: frozenset[str]

    @classmethod
    def from_env(cls):
        token, chat = (
            os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        )
        users = frozenset(
            value.strip()
            for value in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
            if value.strip()
        )
        if not users and chat.isdigit():
            users = frozenset({chat})
        if not token or not chat or not users:
            raise PipelineError(
                "telegram_not_configured",
                "Configure Telegram bot, chat, and allowed user IDs (required for groups).",
            )
        return cls(token, chat, users)


class TelegramPreview:
    def __init__(self, settings: TelegramSettings):
        self.settings = settings

    def call(self, method: str, data: dict, files: dict | None = None) -> dict:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.settings.token}/{method}",
                data={"chat_id": self.settings.chat_id, **data},
                files=files,
                timeout=(10, 120),
            )
            result = response.json()
            if response.status_code != 200 or not result.get("ok"):
                raise ValueError
            return result["result"]
        except Exception:  # noqa: BLE001 -- never expose token-bearing Telegram URLs
            raise PipelineError(
                "telegram_delivery_failed",
                "Telegram delivery failed. No YouTube approval was activated.",
            ) from None

    def send(
        self,
        job_id: str,
        nonce: str,
        package: VideoPackage,
        channel_title: str,
        reply_to: int | None,
    ) -> int:
        reply = (
            {
                "reply_parameters": json.dumps(
                    {"message_id": reply_to, "allow_sending_without_reply": True}
                )
            }
            if reply_to
            else {}
        )
        with (package.directory / "packaging/thumbnail.png").open("rb") as image:
            self.call(
                "sendPhoto",
                {**reply, "caption": f"YouTube thumbnail\n{package.title}"},
                {"photo": ("thumbnail.png", image, "image/png")},
            )
        with (package.directory / "preview.mp4").open("rb") as video:
            self.call(
                "sendVideo",
                {
                    **reply,
                    "caption": "YouTube preview — first 30 seconds only. Accept uploads the full video.",
                    "supports_streaming": "true",
                },
                {"video": ("preview.mp4", video, "video/mp4")},
            )
        # Review the entire final description, not a shortened/different caption.
        for offset in range(0, len(package.description), 3500):
            self.call(
                "sendMessage",
                {**reply, "text": package.description[offset : offset + 3500]},
            )
        prefix = f"tft:{UUID(job_id).hex}:{nonce}:"
        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "Accept · Publish to YouTube",
                        "callback_data": prefix + "a",
                    },
                    {"text": "Cancel", "callback_data": prefix + "c"},
                ]
            ]
        }
        result = self.call(
            "sendMessage",
            {
                **reply,
                "text": f"YouTube approval\n\n{package.title}\n\nChannel: {channel_title}\nVisibility: {package.privacy}\n\nAccept publishes the FULL video to YouTube only. Cancel stops this YouTube publication. Facebook/Instagram carousel approval is separate.",
                "reply_markup": json.dumps(keyboard),
            },
        )
        return int(result["message_id"])

    def notice(self, text: str, message_id: int | None = None):
        data = {"text": text}
        if message_id:
            data["reply_parameters"] = json.dumps(
                {"message_id": message_id, "allow_sending_without_reply": True}
            )
        self.call("sendMessage", data)
