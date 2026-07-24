#!/usr/bin/env python3
"""Send a Bits Today review package to the configured Telegram chat."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


TELEGRAM_API_ROOT = "https://api.telegram.org"
MAX_MESSAGE_CHARACTERS = 4096
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_VIDEO_SUFFIXES = {".mp4"}
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


def validate_video(video: Path) -> Path:
    video = video.resolve()
    if not video.is_file():
        raise ValueError(f"Video does not exist: {video}")
    if video.suffix.lower() not in ALLOWED_VIDEO_SUFFIXES:
        raise ValueError("Telegram preview video must be MP4.")
    if video.stat().st_size <= 0:
        raise ValueError("Telegram preview video cannot be empty.")
    return video


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


def reply_parameters(reply_to_message_id: int | None) -> dict[str, str]:
    if reply_to_message_id is None:
        return {}
    return {
        "reply_parameters": json.dumps(
            {
                "message_id": reply_to_message_id,
                "allow_sending_without_reply": False,
            },
            separators=(",", ":"),
        )
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_preview_receipt(
    path: Path,
    *,
    job_id: str | None,
    reply_to_message_id: int | None,
    images: list[Path],
    videos: list[Path] | None = None,
    description: str,
    telegram_result: dict[str, Any],
) -> Path:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    payload = {
        "job_id": job_id,
        "stage": "preview",
        "reply_to_message_id": reply_to_message_id,
        "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "images": [str(image.resolve()) for image in images],
        "image_sha256s": [sha256_file(image) for image in images],
        "videos": [str(video.resolve()) for video in videos or []],
        "video_sha256s": [sha256_file(video) for video in videos or []],
        "description_sha256": hashlib.sha256(description.encode("utf-8")).hexdigest(),
        **telegram_result,
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)
    return path


def send_video_review_package(
    session: requests.Session,
    config: TelegramConfig,
    *,
    video: Path,
    description: str,
    stage: str,
    reply_to_message_id: int | None = None,
) -> dict[str, Any]:
    video = validate_video(video)
    description = validate_description(description)
    label = STAGE_LABELS[stage]
    mime_type = mimetypes.guess_type(video.name)[0] or "video/mp4"
    with video.open("rb") as video_file:
        video_result = call_telegram(
            session,
            config,
            "sendVideo",
            data={
                "chat_id": config.chat_id,
                "caption": f"Bits Today — {label} — REEL",
                "supports_streaming": "true",
                **reply_parameters(reply_to_message_id),
            },
            files={"video": (video.name, video_file, mime_type)},
            timeout=(10, 180),
        )
    text_results = []
    for chunk in split_message(f"Bits Today — {label}\n\n{description}"):
        text_results.append(
            call_telegram(
                session,
                config,
                "sendMessage",
                data={
                    "chat_id": config.chat_id,
                    "text": chunk,
                    "disable_web_page_preview": "true",
                    **reply_parameters(reply_to_message_id),
                },
                timeout=(10, 30),
            )
        )
    return {
        "video_message_id": video_result.get("message_id"),
        "video_message_ids": [video_result.get("message_id")],
        "photo_message_ids": [],
        "description_message_ids": [item.get("message_id") for item in text_results],
        "reply_to_message_id": reply_to_message_id,
    }


def send_review_package(
    session: requests.Session,
    config: TelegramConfig,
    *,
    image: Path,
    description: str,
    stage: str,
    secondary_images: list[Path] | None = None,
    reply_to_message_id: int | None = None,
) -> dict[str, Any]:
    images = [validate_image(image)]
    images.extend(validate_image(item) for item in secondary_images or [])
    description = validate_description(description)
    label = STAGE_LABELS[stage]

    photo_results = []
    for index, current_image in enumerate(images):
        media_type = (
            mimetypes.guess_type(current_image.name)[0]
            or "application/octet-stream"
        )
        image_label = "MAIN IMAGE" if index == 0 else f"SOURCE IMAGE {index}"
        with current_image.open("rb") as image_file:
            photo_results.append(
                call_telegram(
                    session,
                    config,
                    "sendPhoto",
                    data={
                        "chat_id": config.chat_id,
                        "caption": f"Bits Today — {label} — {image_label}",
                        **reply_parameters(reply_to_message_id),
                    },
                    files={
                        "photo": (current_image.name, image_file, media_type)
                    },
                )
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
                    **reply_parameters(reply_to_message_id),
                },
                timeout=(10, 30),
            )
        )

    return {
        "photo_message_id": photo_results[0].get("message_id"),
        "photo_message_ids": [item.get("message_id") for item in photo_results],
        "description_message_ids": [item.get("message_id") for item in text_results],
        "reply_to_message_id": reply_to_message_id,
    }


def watcher_reply_message_id() -> int | None:
    raw_value = os.getenv("TELEGRAM_REPLY_TO_MESSAGE_ID", "").strip()
    if not raw_value:
        return None
    if not raw_value.isdigit() or int(raw_value) <= 0:
        raise ValueError("TELEGRAM_REPLY_TO_MESSAGE_ID must be a positive integer.")
    return int(raw_value)


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
    media_group = parser.add_mutually_exclusive_group()
    media_group.add_argument("--image", type=Path, help="Local review image.")
    media_group.add_argument("--video", type=Path, help="Local MP4 reel preview.")
    parser.add_argument(
        "--secondary-image",
        action="append",
        default=[],
        type=Path,
        help="Additional source image to preview after --image; repeat as needed.",
    )
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

            if (args.image is None and args.video is None) or not (
                args.description or args.description_file
            ):
                raise ValueError(
                    "--image or --video and either --description or --description-file are required."
                )
            if args.video is not None and args.secondary_image:
                raise ValueError("--secondary-image cannot be used with --video.")
            image = validate_image(args.image) if args.image is not None else None
            video = validate_video(args.video) if args.video is not None else None
            secondary_images = [
                validate_image(item) for item in args.secondary_image
            ]
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
                            "image": str(image) if image is not None else None,
                            "video": str(video) if video is not None else None,
                            "secondary_images": [
                                str(item) for item in secondary_images
                            ],
                            "image_count": (1 + len(secondary_images)) if image else 0,
                            "description_characters": len(description),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0

            if video is not None:
                result = send_video_review_package(
                    session,
                    config,
                    video=video,
                    description=description,
                    stage=args.stage,
                    reply_to_message_id=watcher_reply_message_id(),
                )
            else:
                assert image is not None
                result = send_review_package(
                    session,
                    config,
                    image=image,
                    description=description,
                    stage=args.stage,
                    secondary_images=secondary_images,
                    reply_to_message_id=watcher_reply_message_id(),
                )
            receipt_path = None
            configured_receipt = os.getenv("TELEGRAM_PREVIEW_RECEIPT_PATH", "").strip()
            if args.stage == "preview" and configured_receipt:
                receipt_path = write_preview_receipt(
                    Path(configured_receipt),
                    job_id=os.getenv("TELEGRAM_WATCHER_JOB_ID", "").strip() or None,
                    reply_to_message_id=result["reply_to_message_id"],
                    images=([image, *secondary_images] if image is not None else []),
                    videos=([video] if video is not None else []),
                    description=description,
                    telegram_result=result,
                )
            print(
                json.dumps(
                    {
                        "validated": True,
                        "sent": True,
                        "bot_username": bot.get("username"),
                        "stage": args.stage,
                        "preview_receipt": (
                            str(receipt_path) if receipt_path is not None else None
                        ),
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
