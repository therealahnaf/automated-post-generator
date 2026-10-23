"""Local durable job queue, independent of the website's PostgreSQL schema."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import LEASE_SECONDS
from .errors import IdempotencyConflict, QueueFull
from .schemas import CreateVideo, JobView

PREFIX = "/api/internal/story-videos"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobRepository:
    def __init__(self, directory: Path, queue_limit: int = 20):
        self.directory = directory
        self.queue_limit = queue_limit

    @contextmanager
    def connect(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.directory.chmod(0o700)
        conn = sqlite3.connect(
            self.directory / "jobs.sqlite3", timeout=10, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            if os.name != "nt":
                for name in ("jobs.sqlite3", "jobs.sqlite3-wal", "jobs.sqlite3-shm"):
                    path = self.directory / name
                    if path.exists():
                        path.chmod(0o600)
            conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, key_hash TEXT NOT NULL UNIQUE,
                request_hash TEXT NOT NULL, request_json TEXT NOT NULL,
                status TEXT NOT NULL, stage TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                completed_segments INTEGER NOT NULL DEFAULT 0,
                total_segments INTEGER NOT NULL DEFAULT 0,
                error_code TEXT, duration_seconds REAL,
                owner TEXT, lease_until REAL
            )""")
            yield conn
        finally:
            if conn.in_transaction:
                conn.rollback()
            conn.close()

    @staticmethod
    def reap(conn) -> None:
        # Interrupted paid requests are NOT automatically retried or requeued.
        conn.execute(
            """UPDATE jobs SET status='failed', stage='interrupted',
            error_code='worker_interrupted', updated_at=?, owner=NULL, lease_until=NULL
            WHERE status='running' AND lease_until < ?""",
            (now_iso(), time.time()),
        )

    def create(self, request: CreateVideo, key: str) -> dict:
        document = json.dumps(
            request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        request_hash = hashlib.sha256(document.encode()).hexdigest()
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.reap(conn)
            old = conn.execute(
                "SELECT * FROM jobs WHERE key_hash=?", (key_hash,)
            ).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise IdempotencyConflict
                conn.commit()
                return dict(old)
            count = conn.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            if count >= self.queue_limit:
                raise QueueFull
            job_id, timestamp = str(uuid4()), now_iso()
            conn.execute(
                """INSERT INTO jobs
                (id,key_hash,request_hash,request_json,status,stage,created_at,updated_at)
                VALUES (?,?,?,?,'queued','queued',?,?)""",
                (job_id, key_hash, request_hash, document, timestamp, timestamp),
            )
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            conn.commit()
            return dict(row)

    def get(self, job_id: str) -> dict | None:
        with self.connect() as conn:
            self.reap(conn)
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            result = dict(row) if row else None
            if (
                result
                and conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='youtube_reviews'"
                ).fetchone()
            ):
                review = conn.execute(
                    "SELECT state,title,last_error,youtube_id,youtube_url FROM youtube_reviews WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                if review:
                    result["review"] = dict(review)
            if result and result["status"] == "completed":
                try:
                    result["title"] = (
                        (self.directory / job_id / "packaging/title.txt")
                        .read_text(encoding="utf-8")
                        .strip()
                    )
                except OSError:
                    pass  # Legacy jobs have no packaging artifacts.
            return result

    def claim(self, owner: str) -> dict | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.reap(conn)
            row = conn.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            if not row:
                conn.commit()
                return None
            conn.execute(
                """UPDATE jobs SET status='running',stage='starting',owner=?,
                lease_until=?,updated_at=? WHERE id=?""",
                (owner, time.time() + LEASE_SECONDS, now_iso(), row["id"]),
            )
            claimed = dict(
                conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
            )
            conn.commit()
            return claimed

    def heartbeat(self, job_id: str, owner: str) -> bool:
        with self.connect() as conn:
            return (
                conn.execute(
                    """UPDATE jobs SET lease_until=?
                WHERE id=? AND owner=? AND status='running'""",
                    (time.time() + LEASE_SECONDS, job_id, owner),
                ).rowcount
                == 1
            )

    def progress(
        self, job_id: str, owner: str, stage: str, completed: int = 0, total: int = 0
    ) -> bool:
        with self.connect() as conn:
            return (
                conn.execute(
                    """UPDATE jobs SET stage=?,completed_segments=?,
                total_segments=?,updated_at=? WHERE id=? AND owner=? AND status='running'""",
                    (stage, completed, total, now_iso(), job_id, owner),
                ).rowcount
                == 1
            )

    def finish(
        self,
        job_id: str,
        owner: str,
        *,
        duration: float | None = None,
        error: str | None = None,
    ) -> None:
        status = "failed" if error else "completed"
        from .review import ReviewRepository

        ReviewRepository(self)  # Additive migration; no old jobs are enrolled.
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                """UPDATE jobs SET status=?,stage=?,duration_seconds=?,error_code=?,
                owner=NULL,lease_until=NULL,updated_at=? WHERE id=? AND owner=? AND status='running'""",
                (status, status, duration, error, now_iso(), job_id, owner),
            ).rowcount
            if changed and not error:
                row = conn.execute(
                    "SELECT request_json FROM jobs WHERE id=?", (job_id,)
                ).fetchone()
                if json.loads(row["request_json"]).get("send_telegram_preview", False):
                    conn.execute(
                        """INSERT OR IGNORE INTO youtube_reviews
                        (job_id,state,nonce,updated_at) VALUES (?,'pending',?,?)""",
                        (job_id, secrets.token_hex(6), now_iso()),
                    )
            conn.commit()


def job_view(row: dict) -> JobView:
    base = f"{PREFIX}/{row['id']}"
    review = row.get("review", {})
    return JobView(
        **{
            key: row[key]
            for key in (
                "id",
                "status",
                "stage",
                "created_at",
                "updated_at",
                "completed_segments",
                "total_segments",
                "error_code",
                "duration_seconds",
            )
        },
        status_url=base,
        video_url=f"{base}/video" if row["status"] == "completed" else None,
        manifest_url=f"{base}/manifest" if row["status"] == "completed" else None,
        thumbnail_url=f"{base}/thumbnail" if row["status"] == "completed" else None,
        preview_url=f"{base}/preview" if row["status"] == "completed" else None,
        title=review.get("title") or row.get("title"),
        review_status=review.get("state", "not_requested"),
        review_error=review.get("last_error"),
        youtube_id=review.get("youtube_id"),
        youtube_url=review.get("youtube_url"),
    )
