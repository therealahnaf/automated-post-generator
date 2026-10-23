"""Durable, independent YouTube approvals. Never consumes carousel approvals."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import PipelineError
from .files import sha256_file
from .repository import JobRepository, now_iso
from .schemas import TelegramDecision

PACKAGE_FILES = (
    "video.mp4",
    "preview.mp4",
    "packaging/title.txt",
    "packaging/thumbnail.png",
    "description.txt",
)


@dataclass(frozen=True)
class VideoPackage:
    directory: Path
    title: str
    description: str
    privacy: str
    channel_id: str
    digest: str

    @classmethod
    def load(cls, directory: Path, privacy: str, channel_id: str) -> VideoPackage:
        try:
            fingerprints = {
                name: sha256_file(directory / name) for name in PACKAGE_FILES
            }
            title = (
                (directory / "packaging/title.txt").read_text(encoding="utf-8").strip()
            )
            description = (
                (directory / "description.txt").read_text(encoding="utf-8").strip()
            )
        except (OSError, ValueError):
            raise PipelineError(
                "package_missing", "The complete video package is missing."
            ) from None
        if (
            not title
            or len(title) > 100
            or "<" in title
            or ">" in title
            or len(description.encode()) > 5000
        ):
            raise PipelineError(
                "package_invalid", "The YouTube title or description is invalid."
            )
        if privacy not in {"public", "private", "unlisted"} or not channel_id:
            raise PipelineError(
                "package_invalid", "The YouTube destination is invalid."
            )
        fingerprints.update(privacy=privacy, channel_id=channel_id)
        digest = hashlib.sha256(
            json.dumps(fingerprints, sort_keys=True).encode()
        ).hexdigest()
        return cls(directory, title, description, privacy, channel_id, digest)


class ReviewRepository:
    """Uses the job database, including private resumable-upload checkpoints."""

    def __init__(self, jobs: JobRepository):
        self.jobs = jobs
        with jobs.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS youtube_reviews (
                job_id TEXT PRIMARY KEY REFERENCES jobs(id),
                state TEXT NOT NULL, nonce TEXT NOT NULL,
                chat_id TEXT, message_id INTEGER, package_hash TEXT, channel_id TEXT,
                privacy TEXT, title TEXT, approved_by TEXT,
                upload_state TEXT NOT NULL DEFAULT '{}',
                youtube_id TEXT, youtube_url TEXT, last_error TEXT,
                owner TEXT, lease_until REAL, updated_at TEXT NOT NULL
            )""")

    def enqueue(self, job_id: str) -> None:
        with self.jobs.connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id=? AND status='completed'", (job_id,)
            ).fetchone()
            # Missing flag means a legacy job: never retroactively send previews.
            if row and json.loads(row["request_json"]).get(
                "send_telegram_preview", False
            ):
                conn.execute(
                    """INSERT OR IGNORE INTO youtube_reviews
                    (job_id,state,nonce,updated_at) VALUES (?,'pending',?,?)""",
                    (job_id, secrets.token_hex(6), now_iso()),
                )

    def get(self, job_id: str) -> dict | None:
        with self.jobs.connect() as conn:
            row = conn.execute(
                "SELECT * FROM youtube_reviews WHERE job_id=?", (job_id,)
            ).fetchone()
            return dict(row) if row else None

    def claim(self, owner: str) -> dict | None:
        with self.jobs.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE youtube_reviews SET
                state=CASE state WHEN 'delivering' THEN 'preview_failed' ELSE 'publication_failed' END,
                last_error='delivery_interrupted',owner=NULL,lease_until=NULL,updated_at=?
                WHERE state IN ('delivering','publishing') AND lease_until < ?""",
                (now_iso(), time.time()),
            )
            row = conn.execute("""SELECT * FROM youtube_reviews WHERE state IN ('approved','pending')
                ORDER BY CASE state WHEN 'approved' THEN 0 ELSE 1 END,updated_at LIMIT 1""").fetchone()
            if not row:
                conn.commit()
                return None
            state = "publishing" if row["state"] == "approved" else "delivering"
            conn.execute(
                """UPDATE youtube_reviews SET state=?,owner=?,lease_until=?,updated_at=? WHERE job_id=?""",
                (state, owner, time.time() + 120, now_iso(), row["job_id"]),
            )
            result = dict(
                conn.execute(
                    "SELECT * FROM youtube_reviews WHERE job_id=?", (row["job_id"],)
                ).fetchone()
            )
            conn.commit()
            return result

    def heartbeat(self, job_id: str, owner: str) -> bool:
        with self.jobs.connect() as conn:
            return (
                conn.execute(
                    """UPDATE youtube_reviews SET lease_until=?
                WHERE job_id=? AND owner=? AND state IN ('delivering','publishing')""",
                    (time.time() + 120, job_id, owner),
                ).rowcount
                == 1
            )

    def update(self, job_id: str, owner: str, **values) -> None:
        allowed = {
            "state",
            "chat_id",
            "message_id",
            "package_hash",
            "channel_id",
            "privacy",
            "title",
            "upload_state",
            "youtube_id",
            "youtube_url",
            "last_error",
        }
        if not values or not set(values) <= allowed:
            raise ValueError("Invalid review update")
        with self.jobs.connect() as conn:
            columns = ",".join(f"{name}=?" for name in values)
            if (
                conn.execute(
                    f"UPDATE youtube_reviews SET {columns},updated_at=? WHERE job_id=? AND owner=?",
                    (*values.values(), now_iso(), job_id, owner),
                ).rowcount
                != 1
            ):
                raise PipelineError(
                    "delivery_interrupted", "The delivery worker lost its lease."
                )

    def decide(
        self,
        job_id: str,
        decision: TelegramDecision,
        allowed_users: frozenset[str],
        chat_id: str,
    ) -> dict:
        with self.jobs.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM youtube_reviews WHERE job_id=?", (job_id,)
            ).fetchone()
            if (
                not row
                or decision.chat_id != chat_id
                or row["chat_id"] != chat_id
                or decision.message_id != row["message_id"]
                or decision.sender_id not in allowed_users
                or not hmac.compare_digest(decision.token, row["nonce"])
            ):
                raise PipelineError(
                    "approval_forbidden", "This is not an authorized preview button."
                )
            if row["state"] != "awaiting_approval":
                raise PipelineError(
                    "approval_stale",
                    "This preview was already handled or is no longer active.",
                )
            state = "cancelled"
            if decision.action == "accept":
                package = VideoPackage.load(
                    self.jobs.directory / job_id, row["privacy"], row["channel_id"]
                )
                if not hmac.compare_digest(package.digest, row["package_hash"]):
                    conn.execute(
                        "UPDATE youtube_reviews SET state='preview_failed',last_error='package_changed' WHERE job_id=?",
                        (job_id,),
                    )
                    conn.commit()
                    raise PipelineError(
                        "package_changed",
                        "The package changed. Send a new preview before approval.",
                    )
                state = "approved"
            conn.execute(
                """UPDATE youtube_reviews SET state=?,approved_by=?,updated_at=?,owner=NULL,
                lease_until=NULL WHERE job_id=?""",
                (state, decision.sender_id, now_iso(), job_id),
            )
            conn.commit()
            return {
                "review_status": state,
                "message": "Full video queued for YouTube only."
                if state == "approved"
                else "YouTube publication cancelled. The carousel is unchanged.",
            }

    def retry(self, job_id: str) -> None:
        """Explicitly retry delivery/checkpoint recovery, never grant new approval."""
        with self.jobs.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM youtube_reviews WHERE job_id=?", (job_id,)
            ).fetchone()
            if not row or row["state"] not in {
                "preview_failed",
                "publication_failed",
                "published_thumbnail_failed",
            }:
                raise PipelineError(
                    "retry_forbidden", "This job is not eligible for delivery recovery."
                )
            preview = row["state"] == "preview_failed"
            conn.execute(
                """UPDATE youtube_reviews SET state=?,nonce=?,last_error=NULL,owner=NULL,
                lease_until=NULL,updated_at=? WHERE job_id=?""",
                (
                    "pending" if preview else "approved",
                    secrets.token_hex(6) if preview else row["nonce"],
                    now_iso(),
                    job_id,
                ),
            )
            conn.commit()
