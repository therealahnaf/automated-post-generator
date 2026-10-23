"""Durable video acceptance followed by a user-supplied YouTube title."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .errors import PipelineError
from .files import sha256_file
from .repository import JobRepository, now_iso
from .schemas import TelegramDecision, TelegramTitleReply

PACKAGE_FILES = (
    "video.mp4",
    "preview.mp4",
    "packaging/thumbnail.png",
    "description.txt",
)
TITLE_WAIT_STATES = {
    "title_pending",
    "prompting_title",
    "awaiting_title",
    "title_prompt_failed",
}


def validate_title(text: str) -> str:
    """Preserve the supplied wording; only trim surrounding whitespace."""
    title = text.strip()
    if (
        not title
        or len(title) > 100
        or any(
            char in "<>" or unicodedata.category(char) in {"Cc", "Zl", "Zp"}
            for char in title
        )
    ):
        raise PipelineError(
            "title_invalid",
            "Please reply to the title request with one line of 1–100 characters, without < or >. Nothing was uploaded.",
        )
    return title


@dataclass(frozen=True)
class VideoPackage:
    directory: Path
    title: str
    description: str
    privacy: str
    channel_id: str
    digest: str

    @classmethod
    def load(
        cls,
        directory: Path,
        privacy: str,
        channel_id: str,
        title: str = "",
        *,
        legacy: bool = False,
    ) -> VideoPackage:
        try:
            fingerprints = {
                name: sha256_file(directory / name) for name in PACKAGE_FILES
            }
            if legacy:
                # Recovery of uploads already started before manual titles existed.
                fingerprints["packaging/title.txt"] = sha256_file(
                    directory / "packaging/title.txt"
                )
                title = (
                    (directory / "packaging/title.txt")
                    .read_text(encoding="utf-8")
                    .strip()
                )
            else:
                fingerprints["title"] = title
            description = (
                (directory / "description.txt").read_text(encoding="utf-8").strip()
            )
        except (OSError, ValueError):
            raise PipelineError(
                "package_missing", "The complete video package is missing."
            ) from None
        if title:
            validate_title(title)
        if not description or len(description.encode()) > 5000:
            raise PipelineError(
                "package_invalid", "The YouTube description is invalid."
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
    """Private SQLite state, including exact title replies and upload checkpoints."""

    def __init__(self, jobs: JobRepository):
        self.jobs = jobs
        with jobs.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""CREATE TABLE IF NOT EXISTS youtube_reviews (
                job_id TEXT PRIMARY KEY REFERENCES jobs(id),
                state TEXT NOT NULL, nonce TEXT NOT NULL,
                chat_id TEXT, message_id INTEGER, package_hash TEXT, channel_id TEXT,
                privacy TEXT, title TEXT, approved_by TEXT,
                upload_state TEXT NOT NULL DEFAULT '{}',
                youtube_id TEXT, youtube_url TEXT, last_error TEXT,
                owner TEXT, lease_until REAL, updated_at TEXT NOT NULL,
                flow_version INTEGER NOT NULL DEFAULT 2,
                title_prompt_id INTEGER, title_message_id INTEGER
            )""")
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(youtube_reviews)")
            }
            for name, definition in (
                ("flow_version", "INTEGER NOT NULL DEFAULT 1"),
                ("title_prompt_id", "INTEGER"),
                ("title_message_id", "INTEGER"),
            ):
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE youtube_reviews ADD COLUMN {name} {definition}"
                    )
            conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS youtube_title_prompt
                ON youtube_reviews(chat_id,title_prompt_id) WHERE title_prompt_id IS NOT NULL""")
            conn.commit()

    def enqueue(self, job_id: str) -> None:
        with self.jobs.connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id=? AND status='completed'", (job_id,)
            ).fetchone()
            if row and json.loads(row["request_json"]).get(
                "send_telegram_preview", False
            ):
                conn.execute(
                    """INSERT OR IGNORE INTO youtube_reviews
                    (job_id,state,nonce,updated_at,flow_version) VALUES (?,'pending',?,?,2)""",
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
                """UPDATE youtube_reviews SET state=CASE state
                WHEN 'delivering' THEN 'preview_failed'
                WHEN 'prompting_title' THEN 'title_prompt_failed' ELSE 'publication_failed' END,
                last_error='delivery_interrupted',owner=NULL,lease_until=NULL,updated_at=?
                WHERE state IN ('delivering','prompting_title','publishing') AND lease_until < ?""",
                (now_iso(), time.time()),
            )
            # Old generated titles never authorize a NEW upload after this upgrade.
            # Already-started uploads retain their original hashes/checkpoints for recovery.
            old_reviews = conn.execute("""SELECT * FROM youtube_reviews WHERE flow_version=1
                AND state IN ('pending','awaiting_approval','approved','preview_failed','publication_failed')
                AND youtube_id IS NULL""").fetchall()
            for old in old_reviews:
                saved = json.loads(old["upload_state"])
                if (
                    saved.get("started")
                    or saved.get("upload_uri")
                    or saved.get("video_id")
                ):
                    continue
                conn.execute(
                    """UPDATE youtube_reviews SET flow_version=2,state='pending',nonce=?,
                    title=NULL,approved_by=NULL,package_hash=NULL,last_error=NULL,owner=NULL,
                    lease_until=NULL,updated_at=? WHERE job_id=?""",
                    (secrets.token_hex(6), now_iso(), old["job_id"]),
                )
            row = conn.execute("""SELECT * FROM youtube_reviews
                WHERE state IN ('approved','title_pending','pending')
                ORDER BY CASE state WHEN 'approved' THEN 0 WHEN 'title_pending' THEN 1 ELSE 2 END,
                updated_at LIMIT 1""").fetchone()
            if not row:
                conn.commit()
                return None
            state = {
                "approved": "publishing",
                "title_pending": "prompting_title",
                "pending": "delivering",
            }[row["state"]]
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
                    """UPDATE youtube_reviews SET lease_until=? WHERE job_id=? AND owner=?
                AND state IN ('delivering','prompting_title','publishing')""",
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
            "title_prompt_id",
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

    def _unchanged(self, conn, row, title: str = "") -> VideoPackage:
        package = VideoPackage.load(
            self.jobs.directory / row["job_id"],
            row["privacy"],
            row["channel_id"],
            title,
        )
        if not hmac.compare_digest(package.digest, row["package_hash"] or ""):
            conn.execute(
                """UPDATE youtube_reviews SET state='preview_failed',last_error='package_changed',
                owner=NULL,lease_until=NULL,updated_at=? WHERE job_id=?""",
                (now_iso(), row["job_id"]),
            )
            conn.commit()
            raise PipelineError(
                "package_changed",
                "The package changed. Send a new preview before approval.",
            )
        return package

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
            prompt_cancel = (
                row
                and decision.action == "cancel"
                and decision.message_id == row["title_prompt_id"]
            )
            if (
                not row
                or decision.chat_id != chat_id
                or row["chat_id"] != chat_id
                or (decision.message_id != row["message_id"] and not prompt_cancel)
                or decision.sender_id not in allowed_users
                or not hmac.compare_digest(decision.token, row["nonce"])
            ):
                raise PipelineError(
                    "approval_forbidden", "This is not an authorized preview button."
                )
            if row["state"] != "awaiting_approval" and not (
                decision.action == "cancel" and row["state"] in TITLE_WAIT_STATES
            ):
                raise PipelineError(
                    "approval_stale",
                    "This preview was already handled or is no longer active.",
                )
            state = "cancelled"
            if decision.action == "accept":
                self._unchanged(conn, row)
                state = "title_pending"
            conn.execute(
                """UPDATE youtube_reviews SET state=?,approved_by=?,updated_at=?,owner=NULL,
                lease_until=NULL WHERE job_id=?""",
                (state, decision.sender_id, now_iso(), job_id),
            )
            conn.commit()
            return {
                "review_status": state,
                "message": "Video accepted. The bot will ask you to reply with the YouTube title. Nothing uploads until then."
                if state == "title_pending"
                else "YouTube publication cancelled. The carousel is unchanged.",
            }

    def submit_title(
        self, reply: TelegramTitleReply, allowed_users: frozenset[str], chat_id: str
    ) -> dict:
        with self.jobs.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM youtube_reviews WHERE chat_id=? AND title_prompt_id=?",
                (reply.chat_id, reply.reply_to_message_id),
            ).fetchone()
            if (
                not row
                or reply.chat_id != chat_id
                or reply.sender_id not in allowed_users
                or reply.sender_id != row["approved_by"]
            ):
                raise PipelineError(
                    "approval_forbidden",
                    "This is not your active YouTube title request.",
                )
            title = validate_title(reply.text)
            # Lost HTTP responses/duplicate updates must not create another upload or change the title.
            if row["title_message_id"] == reply.message_id and row["title"] == title:
                return self._title_receipt(title, duplicate=True)
            if row["state"] != "awaiting_title":
                raise PipelineError(
                    "approval_stale",
                    "This title request was already handled or cancelled.",
                )
            self._unchanged(conn, row)
            package = VideoPackage.load(
                self.jobs.directory / row["job_id"],
                row["privacy"],
                row["channel_id"],
                title,
            )
            conn.execute(
                """UPDATE youtube_reviews SET state='approved',title=?,title_message_id=?,
                package_hash=?,last_error=NULL,updated_at=?,owner=NULL,lease_until=NULL WHERE job_id=?""",
                (title, reply.message_id, package.digest, now_iso(), row["job_id"]),
            )
            conn.commit()
            return self._title_receipt(title)

    @staticmethod
    def _title_receipt(title: str, *, duplicate: bool = False) -> dict:
        return {
            "review_status": "approved",
            "title": title,
            "message": f"Title saved: {title}\n"
            + (
                "This reply was already processed. No additional upload was queued."
                if duplicate
                else "Full video queued for YouTube only."
            ),
        }

    def retry(self, job_id: str) -> None:
        """Delivery/checkpoint recovery never grants new approval."""
        with self.jobs.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM youtube_reviews WHERE job_id=?", (job_id,)
            ).fetchone()
            states = {
                "preview_failed": "pending",
                "title_prompt_failed": "title_pending",
                "publication_failed": "approved",
                "published_thumbnail_failed": "approved",
            }
            if not row or row["state"] not in states:
                raise PipelineError(
                    "retry_forbidden", "This job is not eligible for delivery recovery."
                )
            preview = row["state"] == "preview_failed"
            conn.execute(
                """UPDATE youtube_reviews SET state=?,nonce=?,last_error=NULL,owner=NULL,
                lease_until=NULL,title_prompt_id=CASE WHEN ? THEN NULL ELSE title_prompt_id END,
                title_message_id=CASE WHEN ? THEN NULL ELSE title_message_id END,
                title=CASE WHEN ? THEN NULL ELSE title END,
                approved_by=CASE WHEN ? THEN NULL ELSE approved_by END,updated_at=? WHERE job_id=?""",
                (
                    states[row["state"]],
                    secrets.token_hex(6) if preview else row["nonce"],
                    preview,
                    preview,
                    preview,
                    preview,
                    now_iso(),
                    job_id,
                ),
            )
            conn.commit()
