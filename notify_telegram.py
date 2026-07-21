#!/usr/bin/env python3
"""Send a Bits Today review package to the configured Telegram chat."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"))


TELEGRAM_API_ROOT = "https://api.telegram.org"
MAX_MESSAGE_CHARACTERS = 4096
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
STAGE_LABELS = {
    "preview": "PREVIEW REVIEW",
    "final": "FINAL PUBLISHING APPROVAL",
}


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


def load_config(*, require_chat_id: bool = True) -> TelegramConfig:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env.")
    if require_chat_id and not chat_id:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID is not set. Send /start to the bot, then run "
            "notify_telegram.py --discover-chat."
        )
    return TelegramConfig(bot_token=bot_token, chat_id=chat_id)


def method_url(config: TelegramConfig, method: str) -> str:
    return f"{TELEGRAM_API_ROOT}/bot{config.bot_token}/{method}"


def parse_telegram_response(response: requests.Response) -> Any:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Telegram API returned non-JSON HTTP {response.status_code}."
        ) from exc
    if not response.ok or not payload.get("ok"):
        error_code = payload.get("error_code", response.status_code)
        description = payload.get("description", "Unknown Telegram API error")
        raise RuntimeError(f"Telegram API error {error_code}: {description}")
    return payload.get("result")


def call_telegram(
    session: requests.Session,
    config: TelegramConfig,
    method: str,
    *,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    timeout: tuple[int, int] = (10, 60),
) -> Any:
    try:
        response = session.post(
            method_url(config, method),
            data=data,
            files=files,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        # Do not include the request URL because Telegram embeds the bot token in it.
        raise RuntimeError(
            f"Telegram request failed while calling {method}: "
            f"{type(exc).__name__}."
        ) from exc
    return parse_telegram_response(response)


def verify_bot(
    session: requests.Session,
    config: TelegramConfig,
) -> dict[str, Any]:
    result = call_telegram(session, config, "getMe", timeout=(10, 30))
    if not isinstance(result, dict) or not result.get("is_bot"):
        raise RuntimeError("The configured Telegram token did not identify a bot.")
    return result


def verify_chat(
    session: requests.Session,
    config: TelegramConfig,
) -> dict[str, Any]:
    result = call_telegram(
        session,
        config,
        "getChat",
        data={"chat_id": config.chat_id},
        timeout=(10, 30),
    )
    if not isinstance(result, dict):
        raise RuntimeError("Telegram returned invalid chat details.")
    return result


def discover_private_chats(
    session: requests.Session,
    config: TelegramConfig,
) -> list[dict[str, Any]]:
    result = call_telegram(
        session,
        config,
        "getUpdates",
        data={
            "timeout": 0,
            "limit": 100,
            "allowed_updates": json.dumps(["message"]),
        },
        timeout=(10, 30),
    )
    chats: dict[str, dict[str, Any]] = {}
    for update in result or []:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private" or chat.get("id") is None:
            continue
        chat_id = str(chat["id"])
        chats[chat_id] = {
            "chat_id": chat_id,
            "username": chat.get("username"),
            "first_name": chat.get("first_name"),
        }
    return list(chats.values())


def validate_image(image: Path) -> Path:
    image = image.resolve()
    if not image.is_file():
        raise ValueError(f"Image does not exist: {image}")
    if image.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError("Telegram image must be JPEG, PNG, or WebP.")
    return image


def validate_description(description: str) -> str:
    description = description.strip()
    if not description:
        raise ValueError("The Telegram review description cannot be empty.")
    return description


def split_message(text: str, limit: int = MAX_MESSAGE_CHARACTERS) -> list[str]:
    """Split text into Telegram-safe chunks, preferring paragraph boundaries."""
    if limit <= 0:
        raise ValueError("Message limit must be positive.")
    text = text.strip()
    if not text:
        return []

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[: limit + 1]
        split_at = window.rfind("\n\n", 0, limit + 1)
        separator_length = 2
        if split_at <= 0:
            split_at = window.rfind("\n", 0, limit + 1)
            separator_length = 1
        if split_at <= 0:
            split_at = window.rfind(" ", 0, limit + 1)
            separator_length = 1
        if split_at <= 0:
            split_at = limit
            separator_length = 0
        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at + separator_length :].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def send_review_package(
    session: requests.Session,
    config: TelegramConfig,
    *,
    image: Path,
    description: str,
    stage: str,
) -> dict[str, Any]:
    image = validate_image(image)
    description = validate_description(description)
    label = STAGE_LABELS[stage]

    media_type = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
    with image.open("rb") as image_file:
        photo_result = call_telegram(
            session,
            config,
            "sendPhoto",
            data={"chat_id": config.chat_id, "caption": f"Bits Today — {label}"},
            files={"photo": (image.name, image_file, media_type)},
        )

    text = f"Bits Today — {label}\n\n{description}"
    text_results = []
    for chunk in split_message(text):
        text_results.append(
            call_telegram(
                session,
                config,
                "sendMessage",
                data={
                    "chat_id": config.chat_id,
                    "text": chunk,
                    "disable_web_page_preview": "true",
                },
                timeout=(10, 30),
            )
        )

    return {
        "photo_message_id": photo_result.get("message_id"),
        "description_message_ids": [item.get("message_id") for item in text_results],
    }


def read_description(args: argparse.Namespace) -> str:
    if args.description_file:
        return validate_description(
            args.description_file.read_text(encoding="utf-8")
        )
    return validate_description(args.description or "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or send a Bits Today image-and-description review package "
            "to Telegram. Sending is disabled unless --send is supplied."
        )
    )
    parser.add_argument(
        "--discover-chat",
        action="store_true",
        help="List private chats that have messaged the bot, then exit.",
    )
    parser.add_argument("--image", type=Path, help="Local review image.")
    description_group = parser.add_mutually_exclusive_group()
    description_group.add_argument("--description", help="Review description.")
    description_group.add_argument(
        "--description-file",
        type=Path,
        help="UTF-8 file containing the review description.",
    )
    parser.add_argument(
        "--stage",
        choices=sorted(STAGE_LABELS),
        default="preview",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually send the image and description after validation.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(require_chat_id=not args.discover_chat)
        with requests.Session() as session:
            bot = verify_bot(session, config)
            if args.discover_chat:
                print(
                    json.dumps(
                        {
                            "bot_username": bot.get("username"),
                            "private_chats": discover_private_chats(session, config),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0

            if args.image is None or not (args.description or args.description_file):
                raise ValueError(
                    "--image and either --description or --description-file are required."
                )
            image = validate_image(args.image)
            description = read_description(args)
            chat = verify_chat(session, config)
            if not args.send:
                print(
                    json.dumps(
                        {
                            "validated": True,
                            "sent": False,
                            "bot_username": bot.get("username"),
                            "chat_type": chat.get("type"),
                            "stage": args.stage,
                            "image": str(image),
                            "description_characters": len(description),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0

            result = send_review_package(
                session,
                config,
                image=image,
                description=description,
                stage=args.stage,
            )
            print(
                json.dumps(
                    {
                        "validated": True,
                        "sent": True,
                        "bot_username": bot.get("username"),
                        "stage": args.stage,
                        **result,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
