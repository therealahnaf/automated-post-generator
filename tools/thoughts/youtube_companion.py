"""Small HTTP/outbox adapter for the existing Telegram watcher, not a poller."""

from __future__ import annotations

import os
import re
import time
from urllib.parse import urlparse
from uuid import UUID

import requests

CALLBACK = re.compile(r"tft:([a-f0-9]{32}):([a-f0-9]{12}):([ac])")
STATUS_URL = re.compile(
    r"https?://(?:www\.)?(?:x\.com|twitter\.com)/[^/\s]+/status/\d+(?:\?[^\s]*)?",
    re.IGNORECASE,
)


def initialize(connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS youtube_companions (
        watcher_job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
        state TEXT NOT NULL DEFAULT 'pending', story_job_id TEXT,
        attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
        last_error TEXT
    )""")


def enqueue(connection, job) -> None:
    if job["workflow_type"] == "informative" and STATUS_URL.search(job["request_text"]):
        initialize(connection)
        connection.execute(
            "INSERT OR IGNORE INTO youtube_companions(watcher_job_id) VALUES (?)",
            (job["id"],),
        )


def api_settings() -> tuple[str, dict]:
    base = (
        os.getenv("STORY_VIDEO_API_URL") or os.getenv("CONTENT_API_URL", "")
    ).rstrip("/")
    key = os.getenv("CONTENT_API_KEY", "").strip()
    parsed = urlparse(base)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or not key
        or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        )
    ):
        raise RuntimeError(
            "Configure STORY_VIDEO_API_URL (or CONTENT_API_URL) and CONTENT_API_KEY."
        )
    return base + "/api/internal/story-videos", {"Authorization": "Bearer " + key}


def flush_one(connection, session) -> dict | None:
    """Retry HTTP queue submission with the SAME key; never rerun paid generation."""
    initialize(connection)
    row = connection.execute(
        """SELECT c.*,j.chat_id,j.source_message_id,j.request_text
        FROM youtube_companions c JOIN jobs j ON j.id=c.watcher_job_id
        WHERE c.state='pending' AND c.next_attempt <= ? ORDER BY c.watcher_job_id LIMIT 1""",
        (time.time(),),
    ).fetchone()
    if not row:
        return None
    try:
        url, headers = api_settings()
        response = session.post(
            url,
            headers={
                **headers,
                "Idempotency-Key": f"informative-youtube:{row['chat_id']}:{row['source_message_id']}",
            },
            json={
                "x_url": STATUS_URL.search(row["request_text"])[0],
                "send_telegram_preview": True,
                "telegram_reply_to_message_id": row["source_message_id"],
                "speech_speed": 1.3,
            },
            timeout=(3, 10),
        )
        if response.status_code != 202:
            raise ValueError
        job_id = str(UUID(response.json()["id"]))
        connection.execute(
            """UPDATE youtube_companions SET state='submitted',story_job_id=?,last_error=NULL
            WHERE watcher_job_id=?""",
            (job_id, row["watcher_job_id"]),
        )
        result = {
            "submitted": True,
            "reply_to": row["source_message_id"],
            "job_id": job_id,
        }
    except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError):
        attempts = row["attempts"] + 1
        connection.execute(
            """UPDATE youtube_companions SET attempts=?,next_attempt=?,last_error='submission_failed'
            WHERE watcher_job_id=?""",
            (
                attempts,
                time.time() + min(300, 5 * 2 ** min(attempts, 6)),
                row["watcher_job_id"],
            ),
        )
        result = (
            {"submitted": False, "reply_to": row["source_message_id"]}
            if attempts == 1
            else None
        )
    connection.commit()
    return result


def forward_decision(session, callback) -> dict:
    match = CALLBACK.fullmatch(callback.data)
    if not match:
        raise ValueError("Invalid YouTube callback")
    url, headers = api_settings()
    try:
        response = session.post(
            url + "/" + str(UUID(hex=match[1])) + "/decision",
            headers=headers,
            json={
                "action": "accept" if match[3] == "a" else "cancel",
                "token": match[2],
                "chat_id": callback.chat_id,
                "message_id": callback.message_id,
                "sender_id": callback.sender_id or "",
            },
            timeout=(3, 10),
        )
        if response.status_code == 200:
            result = response.json()
            if result.get("review_status") not in {"approved", "cancelled"}:
                raise ValueError
            return result
        if response.status_code in {403, 404, 409}:
            return {
                "review_status": "stale",
                "message": "This YouTube preview is no longer active or authorized.",
            }
        raise ValueError
    except (requests.RequestException, ValueError, KeyError, TypeError):
        raise RuntimeError(
            "YouTube approval service is unavailable. Try the same button again; no new approval was confirmed here."
        ) from None
