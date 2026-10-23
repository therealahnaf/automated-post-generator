"""Deliver previews and approved uploads, independently of slow generation jobs."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import time
from uuid import uuid4

from .config import Settings
from .errors import PipelineError
from .repository import JobRepository
from .review import ReviewRepository, VideoPackage
from .telegram import TelegramPreview, TelegramSettings
from .worker import lease_heartbeat
from .youtube import YouTubePublisher, YouTubeSettings

logger = logging.getLogger(__name__)


def delivery_preflight() -> None:
    TelegramSettings.from_env()
    YouTubeSettings.from_env()


def process_next(reviews: ReviewRepository) -> bool:
    owner = str(uuid4())
    row = reviews.claim(owner)
    if not row:
        return False
    job_id, publishing = row["job_id"], row["state"] == "publishing"
    telegram = None
    try:
        with lease_heartbeat(reviews, job_id, owner) as lost:
            telegram = TelegramPreview(TelegramSettings.from_env())
            settings = YouTubeSettings.from_env()
            job = reviews.jobs.get(job_id)
            request = json.loads(job["request_json"])
            channel_id = row["channel_id"] if publishing else settings.channel_id
            privacy = row["privacy"] if publishing else request["youtube_privacy"]
            package = VideoPackage.load(
                reviews.jobs.directory / job_id, privacy, channel_id
            )

            def update(**values):
                if lost.is_set():
                    raise PipelineError(
                        "delivery_interrupted", "The delivery lease expired."
                    )
                reviews.update(job_id, owner, **values)

            if publishing:
                if not row["approved_by"] or not hmac.compare_digest(
                    package.digest, row["package_hash"]
                ):
                    raise PipelineError(
                        "package_changed",
                        "The approved video package changed. No upload was attempted.",
                    )

                def checkpoint(state):
                    fields = {"upload_state": json.dumps(state)}
                    if state.get("video_id"):
                        fields.update(
                            youtube_id=state["video_id"],
                            youtube_url=f"https://www.youtube.com/watch?v={state['video_id']}",
                        )
                    update(**fields)

                with YouTubePublisher(settings) as publisher:
                    video_id = publisher.publish(
                        package, json.loads(row["upload_state"]), checkpoint
                    )
                update(state="published", last_error=None)
                # Publication success is durable even if a notification fails.
                try:
                    saved = json.loads(reviews.get(job_id)["upload_state"])
                    visibility = saved.get("visibility")
                    label = f" ({visibility})" if visibility else ""
                    telegram.notice(
                        f"YouTube upload complete{label}: {package.title}\nhttps://www.youtube.com/watch?v={video_id}",
                        row["message_id"],
                    )
                except PipelineError:
                    logger.warning(
                        "YouTube %s published; Telegram notification failed", job_id
                    )
            else:
                with YouTubePublisher(settings) as publisher:
                    channel = publisher.channel()  # Read only; preview never uploads.
                update(
                    package_hash=package.digest,
                    channel_id=channel_id,
                    privacy=privacy,
                    title=package.title,
                    chat_id=telegram.settings.chat_id,
                )
                message_id = telegram.send(
                    job_id,
                    row["nonce"],
                    package,
                    channel["title"],
                    request.get("telegram_reply_to_message_id"),
                )
                # No callback is valid until every preview component has arrived.
                update(
                    state="awaiting_approval", message_id=message_id, last_error=None
                )
    except Exception as exc:  # noqa: BLE001 -- persist safe failure codes at the worker boundary
        code = exc.code if isinstance(exc, PipelineError) else "delivery_failed"
        current = reviews.get(job_id)
        state = (
            (
                "published_thumbnail_failed"
                if current.get("youtube_id")
                else "publication_failed"
            )
            if publishing
            else "preview_failed"
        )
        try:
            reviews.update(job_id, owner, state=state, last_error=code)
        except PipelineError:
            logger.error(
                "YouTube delivery %s lost its lease; no retry was queued", job_id
            )
            return True
        logger.error(
            "YouTube delivery %s failed: %s (%s)", job_id, code, type(exc).__name__
        )
        if telegram:
            try:
                url = current.get("youtube_url")
                telegram.notice(
                    f"YouTube companion: {state} ({code}). "
                    + (
                        f"Video already uploaded: {url}. Do not upload it again."
                        if url
                        else "No new approval was granted. The carousel is unaffected."
                    ),
                    row.get("message_id"),
                )
            except PipelineError:
                pass
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    settings = Settings.from_env()
    reviews = ReviewRepository(JobRepository(settings.output_dir, settings.queue_limit))
    try:
        delivery_preflight()
        while True:
            processed = process_next(reviews)
            if args.once:
                return 0
            if not processed:
                time.sleep(2)
    except PipelineError as exc:
        logger.error("YouTube delivery configuration: %s", exc.code)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
