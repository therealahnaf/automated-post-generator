#!/usr/bin/env python3
"""Watch Telegram, generate previews, and resume Codex after reply approval."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import requests
from dotenv import load_dotenv

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None

try:
    from .notify_telegram import split_message
except ImportError:
    from notify_telegram import split_message


REPO_DIR = Path(__file__).resolve().parents[2]
STATE_DIR_NAME = ".automation/watcher"
CRON_BEGIN = "# BEGIN bits-today telegram codex queue"
CRON_END = "# END bits-today telegram codex queue"
ACTIVE_STATUSES = ("generating", "revising", "publishing")
WORKFLOW_TYPES = ("news", "model", "product", "reel", "auto")
CODEX_MODEL = "gpt-5.6-terra"
CODEX_MODEL_REASONING_EFFORT = "medium"
WORKFLOW_LABELS = {
    "news": "News",
    "model": "Model Release",
    "product": "Product Release",
    "reel": "Reel",
    "auto": "Auto Detect",
}
PROGRESS_STAGE_LABELS = {
    "selected": "Workflow selected",
    "fetching": "Fetching and validating source",
    "fetched": "Source fetched and validated",
    "media_ready": "Source media discovered",
    "headline": "Headline generated",
    "research_started": "Researching sources",
    "research_complete": "Research complete",
    "description": "Bilingual description generated",
    "generating_items": "Generating post items",
    "items_ready": "Post items generated",
    "preview": "Preview delivered — awaiting approval",
    "revision": "Applying requested revision",
    "publishing_facebook": "Publishing to Facebook",
    "facebook_done": "Facebook publishing complete",
    "publishing_instagram": "Publishing to Instagram",
    "instagram_done": "Instagram publishing complete",
    "completed": "Workflow complete",
    "failed": "Workflow stopped",
}
X_STATUS_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:x\.com|twitter\.com)/[^/\s]+/status/\d+(?:\?[^\s]*)?",
    re.IGNORECASE,
)
WATCHER_PROMPT_SUFFIX = """This request came from the persistent Telegram watcher.

Follow AGENTS.md through creation and Telegram preview delivery. Do not publish in
this turn. After the latest preview has been sent successfully, stop and wait for
the user to reply exactly `yes` to that preview in Telegram. The watcher will
resume this exact Codex session with that approval. Use
`tools/news/report_progress.py` at every milestone required by AGENTS.md. Do not
use the unattended automatic-publishing exception."""


@dataclass(frozen=True)
class Config:
    telegram_token: str
    telegram_chat_id: str
    allowed_user_ids: frozenset[str]
    repo_dir: Path
    state_dir: Path
    database_path: Path
    codex_bin: Path


@dataclass(frozen=True)
class TelegramMessage:
    update_id: int
    message_id: int
    chat_id: str
    sender_id: str | None
    text: str
    reply_to_message_id: int | None
    received_at: str | None


@dataclass(frozen=True)
class TelegramCallback:
    update_id: int
    callback_id: str
    message_id: int
    chat_id: str
    sender_id: str | None
    data: str


@dataclass(frozen=True)
class CodexResult:
    exit_code: int
    session_id: str | None
    stdout_path: Path
    stderr_path: Path
    final_output_path: Path
    receipt_path: Path
    error: str | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config() -> Config:
    load_dotenv(REPO_DIR / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")

    configured_repo = os.environ.get("CODEX_WATCHER_REPO", "").strip()
    repo_dir = Path(configured_repo or REPO_DIR).resolve()
    configured_state = os.environ.get("CODEX_WATCHER_STATE_DIR", "").strip()
    state_dir = Path(configured_state or repo_dir / STATE_DIR_NAME).resolve()
    configured_database = os.environ.get("CODEX_WATCHER_DB", "").strip()
    database_path = Path(configured_database or state_dir / "watcher.sqlite3").resolve()
    requested_codex = os.environ.get("CODEX_BIN", "").strip()
    discovered_codex = requested_codex or shutil.which("codex") or "/root/.local/bin/codex"
    codex_bin = Path(discovered_codex).resolve()
    if not codex_bin.is_file():
        raise RuntimeError(f"Codex CLI was not found at {codex_bin}")
    allowed_users = frozenset(
        value.strip()
        for value in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
        if value.strip()
    )
    return Config(
        telegram_token=token,
        telegram_chat_id=chat_id,
        allowed_user_ids=allowed_users,
        repo_dir=repo_dir,
        state_dir=state_dir,
        database_path=database_path,
        codex_bin=codex_bin,
    )


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    connection = sqlite3.connect(path, timeout=30)
    path.chmod(0o600)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_update_id INTEGER NOT NULL UNIQUE,
            chat_id TEXT NOT NULL,
            source_message_id INTEGER NOT NULL,
            sender_id TEXT,
            request_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN (
                    'queued', 'generating', 'awaiting_approval', 'revising',
                    'publishing', 'completed', 'failed'
                )),
            session_id TEXT,
            turn_count INTEGER NOT NULL DEFAULT 0,
            received_at TEXT,
            started_at TEXT,
            updated_at TEXT NOT NULL,
            finished_at TEXT,
            last_error TEXT,
            final_output TEXT,
            workflow_type TEXT,
            selector_message_id INTEGER,
            progress_message_id INTEGER,
            UNIQUE(chat_id, source_message_id)
        );

        CREATE INDEX IF NOT EXISTS watcher_jobs_fifo_idx
            ON jobs(status, source_update_id);

        CREATE TABLE IF NOT EXISTS preview_messages (
            message_id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL REFERENCES jobs(id),
            turn_number INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS preview_messages_job_idx
            ON preview_messages(job_id, active);

        CREATE TABLE IF NOT EXISTS telegram_events (
            update_id INTEGER PRIMARY KEY,
            message_id INTEGER,
            job_id INTEGER REFERENCES jobs(id),
            kind TEXT NOT NULL,
            processed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS job_progress_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES jobs(id),
            stage TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS job_progress_events_job_idx
            ON job_progress_events(job_id, id);
        """
    )
    existing_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
    }
    for name, definition in (
        ("workflow_type", "TEXT"),
        ("selector_message_id", "INTEGER"),
        ("progress_message_id", "INTEGER"),
    ):
        if name not in existing_columns:
            connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            if name == "workflow_type":
                connection.execute(
                    """
                    UPDATE jobs SET workflow_type = 'auto'
                    WHERE workflow_type IS NULL
                    """
                )
    connection.commit()
    return connection


def get_setting(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def set_setting(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO settings(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def migrate_queue_offset(config: Config, connection: sqlite3.Connection) -> int:
    current = get_setting(connection, "telegram_offset")
    if current is not None:
        return int(current)
    queue_database = config.repo_dir / ".automation/telegram_codex_queue.sqlite3"
    offset = 0
    if queue_database.is_file():
        old_connection = sqlite3.connect(f"file:{queue_database}?mode=ro", uri=True)
        try:
            row = old_connection.execute(
                "SELECT value FROM settings WHERE key = 'telegram_offset'"
            ).fetchone()
            if row is not None:
                offset = int(row[0])
        finally:
            old_connection.close()
    set_setting(connection, "telegram_offset", str(offset))
    set_setting(connection, "initialized_at", utc_now())
    connection.commit()
    return offset


def fail_interrupted_jobs(connection: sqlite3.Connection) -> int:
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    cursor = connection.execute(
        f"""
        UPDATE jobs
        SET status = 'failed', finished_at = ?, updated_at = ?,
            last_error = 'Watcher restarted during an active Codex turn; manual review required'
        WHERE status IN ({placeholders})
        """,
        (utc_now(), utc_now(), *ACTIVE_STATUSES),
    )
    connection.commit()
    return cursor.rowcount


def telegram_url(config: Config, method: str) -> str:
    return f"https://api.telegram.org/bot{config.telegram_token}/{method}"


def telegram_call(
    session: requests.Session,
    config: Config,
    method: str,
    data: dict[str, Any],
    *,
    timeout: tuple[int, int] = (10, 65),
) -> Any:
    try:
        response = session.post(
            telegram_url(config, method), data=data, timeout=timeout
        )
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Telegram request failed while calling {method}: {type(exc).__name__}"
        ) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Telegram returned non-JSON HTTP {response.status_code} for {method}"
        ) from exc
    if not response.ok or not payload.get("ok"):
        description = payload.get("description", "unknown Telegram error")
        raise RuntimeError(f"Telegram {method} failed: {description}")
    return payload.get("result")


def get_updates(
    session: requests.Session,
    config: Config,
    offset: int,
    *,
    timeout_seconds: int = 50,
) -> list[dict[str, Any]]:
    result = telegram_call(
        session,
        config,
        "getUpdates",
        {
            "offset": str(offset),
            "limit": "100",
            "timeout": str(timeout_seconds),
            "allowed_updates": json.dumps(["message", "callback_query"]),
        },
        timeout=(10, timeout_seconds + 15),
    )
    if not isinstance(result, list):
        raise RuntimeError("Telegram getUpdates returned an invalid result")
    return result


def send_text(
    session: requests.Session,
    config: Config,
    text: str,
    *,
    reply_to_message_id: int,
    reply_markup: dict[str, Any] | None = None,
) -> list[int]:
    message_ids: list[int] = []
    for chunk in split_message(text):
        data = {
            "chat_id": config.telegram_chat_id,
            "text": chunk,
            "disable_web_page_preview": "true",
            "reply_parameters": json.dumps(
                {
                    "message_id": reply_to_message_id,
                    "allow_sending_without_reply": True,
                },
                separators=(",", ":"),
            ),
        }
        if reply_markup is not None and len(split_message(text)) == 1:
            data["reply_markup"] = json.dumps(reply_markup, separators=(",", ":"))
        result = telegram_call(
            session,
            config,
            "sendMessage",
            data,
            timeout=(10, 30),
        )
        if isinstance(result, dict) and isinstance(result.get("message_id"), int):
            message_ids.append(result["message_id"])
    return message_ids


def edit_text(
    session: requests.Session,
    config: Config,
    message_id: int,
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    data: dict[str, Any] = {
        "chat_id": config.telegram_chat_id,
        "message_id": str(message_id),
        "text": text,
        "disable_web_page_preview": "true",
    }
    data["reply_markup"] = json.dumps(
        reply_markup or {"inline_keyboard": []}, separators=(",", ":")
    )
    telegram_call(session, config, "editMessageText", data, timeout=(10, 30))


def answer_callback(
    session: requests.Session,
    config: Config,
    callback_id: str,
    text: str,
) -> None:
    telegram_call(
        session,
        config,
        "answerCallbackQuery",
        {"callback_query_id": callback_id, "text": text},
        timeout=(10, 30),
    )


def workflow_keyboard(job_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "News", "callback_data": f"workflow:{job_id}:news"},
                {"text": "Model Release", "callback_data": f"workflow:{job_id}:model"},
            ],
            [
                {"text": "Product Release", "callback_data": f"workflow:{job_id}:product"},
                {"text": "Reel", "callback_data": f"workflow:{job_id}:reel"},
            ],
            [{"text": "Auto Detect", "callback_data": f"workflow:{job_id}:auto"}],
            [{"text": "Cancel", "callback_data": f"workflow:{job_id}:cancel"}],
        ]
    }


def parse_message(
    update: dict[str, Any], config: Config
) -> TelegramMessage | None:
    update_id = update.get("update_id")
    message = update.get("message")
    if not isinstance(update_id, int) or not isinstance(message, dict):
        return None
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, dict) or str(chat.get("id")) != config.telegram_chat_id:
        return None
    if isinstance(sender, dict) and sender.get("is_bot"):
        return None
    sender_id = str(sender.get("id")) if isinstance(sender, dict) and sender.get("id") else None
    if config.allowed_user_ids and sender_id not in config.allowed_user_ids:
        return None
    message_id = message.get("message_id")
    text = message.get("text")
    if not isinstance(message_id, int) or not isinstance(text, str) or not text.strip():
        return None
    reply = message.get("reply_to_message")
    reply_id = reply.get("message_id") if isinstance(reply, dict) else None
    if not isinstance(reply_id, int):
        reply_id = None
    received_at = None
    if isinstance(message.get("date"), int):
        received_at = datetime.fromtimestamp(message["date"], timezone.utc).isoformat(
            timespec="seconds"
        )
    return TelegramMessage(
        update_id=update_id,
        message_id=message_id,
        chat_id=config.telegram_chat_id,
        sender_id=sender_id,
        text=text.strip(),
        reply_to_message_id=reply_id,
        received_at=received_at,
    )


def parse_callback(
    update: dict[str, Any], config: Config
) -> TelegramCallback | None:
    update_id = update.get("update_id")
    callback = update.get("callback_query")
    if not isinstance(update_id, int) or not isinstance(callback, dict):
        return None
    message = callback.get("message")
    sender = callback.get("from")
    chat = message.get("chat") if isinstance(message, dict) else None
    if not isinstance(chat, dict) or str(chat.get("id")) != config.telegram_chat_id:
        return None
    if isinstance(sender, dict) and sender.get("is_bot"):
        return None
    sender_id = str(sender.get("id")) if isinstance(sender, dict) and sender.get("id") else None
    if config.allowed_user_ids and sender_id not in config.allowed_user_ids:
        return None
    callback_id = callback.get("id")
    message_id = message.get("message_id") if isinstance(message, dict) else None
    data = callback.get("data")
    if not isinstance(callback_id, str) or not isinstance(message_id, int):
        return None
    if not isinstance(data, str):
        return None
    return TelegramCallback(
        update_id=update_id,
        callback_id=callback_id,
        message_id=message_id,
        chat_id=config.telegram_chat_id,
        sender_id=sender_id,
        data=data,
    )


def build_initial_prompt(
    job_id: int, request_text: str, workflow_type: str = "auto"
) -> str:
    if workflow_type not in WORKFLOW_TYPES:
        raise ValueError(f"Unsupported workflow type: {workflow_type}")
    route_instruction = (
        "Run the normal one-time news/model/product classification from AGENTS.md."
        if workflow_type == "auto"
        else (
            f"The user manually selected workflow_type `{workflow_type}`. This "
            "trusted selection is authoritative: persist it and do not reclassify."
        )
    )
    return (
        "Read AGENTS.md\n\n"
        f"{route_instruction}\n\n"
        "TELEGRAM REQUEST START\n"
        f"{request_text.strip()}\n"
        "TELEGRAM REQUEST END\n\n"
        f"Telegram watcher job: {job_id}\n\n{WATCHER_PROMPT_SUFFIX}"
    )


def build_revision_prompt(feedback: str) -> str:
    return (
        "The user replied with revision feedback, not approval. Do not publish. "
        "Apply the feedback to the latest package, send the complete revised preview "
        "to Telegram, and stop for a new exact `yes` approval.\n\n"
        f"Revision feedback:\n{feedback.strip()}"
    )


def parse_session_id(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8", errors="replace") as source:
        for line in source:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started" and isinstance(
                event.get("thread_id"), str
            ):
                return event["thread_id"]
    return None


def codex_command_prefix(config: Config) -> list[str]:
    """Build the common Codex command with the watcher model explicitly pinned."""
    return [
        str(config.codex_bin),
        "--search",
        "--ask-for-approval",
        "never",
        "--model",
        CODEX_MODEL,
        "--config",
        f'model_reasoning_effort="{CODEX_MODEL_REASONING_EFFORT}"',
    ]


def invoke_codex(
    config: Config,
    job: sqlite3.Row,
    *,
    prompt: str,
    reply_to_message_id: int,
    resume: bool,
) -> CodexResult:
    turn = int(job["turn_count"])
    logs_dir = config.state_dir / "logs"
    receipts_dir = config.state_dir / "receipts"
    logs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stem = f"job-{job['id']}-turn-{turn}"
    stdout_path = logs_dir / f"{stem}.jsonl"
    stderr_path = logs_dir / f"{stem}.stderr.log"
    final_output_path = logs_dir / f"{stem}.final.txt"
    receipt_path = receipts_dir / f"{stem}.json"

    if resume:
        command = [
            *codex_command_prefix(config),
            "--sandbox",
            "danger-full-access",
            "exec",
            "resume",
            "--json",
            "--output-last-message",
            str(final_output_path),
            str(job["session_id"]),
            "-",
        ]
    else:
        command = [
            *codex_command_prefix(config),
            "exec",
            "--sandbox",
            "danger-full-access",
            "--json",
            "--cd",
            str(config.repo_dir),
            "--output-last-message",
            str(final_output_path),
            "-",
        ]

    environment = os.environ.copy()
    environment.update(
        {
            "TELEGRAM_REPLY_TO_MESSAGE_ID": str(reply_to_message_id),
            "TELEGRAM_PREVIEW_RECEIPT_PATH": str(receipt_path),
            "TELEGRAM_WATCHER_JOB_ID": str(job["id"]),
            "BITS_TODAY_WORKFLOW_TYPE": str(job["workflow_type"] or "auto"),
        }
    )
    try:
        with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr_file:
            result = subprocess.run(
                command,
                input=prompt,
                text=True,
                cwd=config.repo_dir,
                env=environment,
                stdout=stdout_file,
                stderr=stderr_file,
                check=False,
            )
        exit_code = result.returncode
        error = None if exit_code == 0 else f"Codex exited with status {exit_code}"
    except Exception as exc:
        exit_code = -1
        error = f"Could not run Codex: {type(exc).__name__}: {exc}"
    return CodexResult(
        exit_code=exit_code,
        session_id=parse_session_id(stdout_path),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        final_output_path=final_output_path,
        receipt_path=receipt_path,
        error=error,
    )


def receipt_message_ids(path: Path) -> list[int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = [
        *(payload.get("photo_message_ids") or []),
        *(payload.get("video_message_ids") or []),
        *(payload.get("description_message_ids") or []),
    ]
    message_ids = [value for value in values if isinstance(value, int)]
    if not message_ids:
        raise RuntimeError("The Telegram preview receipt contained no message IDs")
    return list(dict.fromkeys(message_ids))


def register_preview(
    connection: sqlite3.Connection,
    job_id: int,
    turn_number: int,
    receipt_path: Path,
) -> list[int]:
    message_ids = receipt_message_ids(receipt_path)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "UPDATE preview_messages SET active = 0 WHERE job_id = ?", (job_id,)
    )
    for message_id in message_ids:
        connection.execute(
            """
            INSERT OR REPLACE INTO preview_messages(
                message_id, job_id, turn_number, active, created_at
            ) VALUES (?, ?, ?, 1, ?)
            """,
            (message_id, job_id, turn_number, utc_now()),
        )
    connection.execute(
        """
        UPDATE jobs
        SET status = 'awaiting_approval', updated_at = ?, last_error = NULL
        WHERE id = ?
        """,
        (utc_now(), job_id),
    )
    connection.commit()
    return message_ids


def add_active_preview_message(
    connection: sqlite3.Connection, job_id: int, turn_number: int, message_id: int
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO preview_messages(
            message_id, job_id, turn_number, active, created_at
        ) VALUES (?, ?, ?, 1, ?)
        """,
        (message_id, job_id, turn_number, utc_now()),
    )
    connection.commit()


def mark_failed(
    connection: sqlite3.Connection, job_id: int, error: str
) -> None:
    connection.execute(
        """
        UPDATE jobs
        SET status = 'failed', updated_at = ?, finished_at = ?, last_error = ?
        WHERE id = ?
        """,
        (utc_now(), utc_now(), error, job_id),
    )
    connection.commit()


def add_progress_event(
    connection: sqlite3.Connection,
    job_id: int,
    stage: str,
    detail: str | None = None,
) -> None:
    if stage not in PROGRESS_STAGE_LABELS:
        raise ValueError(f"Unsupported progress stage: {stage}")
    normalized_detail = (detail or "").strip() or None
    connection.execute(
        """
        INSERT INTO job_progress_events(job_id, stage, detail, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (job_id, stage, normalized_detail, utc_now()),
    )
    connection.execute(
        "UPDATE jobs SET updated_at = ? WHERE id = ?", (utc_now(), job_id)
    )
    connection.commit()


def render_progress(connection: sqlite3.Connection, job_id: int) -> str:
    job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise ValueError(f"Watcher job {job_id} does not exist")
    events = connection.execute(
        """
        SELECT stage, detail FROM job_progress_events
        WHERE job_id = ? ORDER BY id
        """,
        (job_id,),
    ).fetchall()
    workflow = str(job["workflow_type"] or "awaiting selection")
    lines = [
        f"Bits Today · Job {job_id}",
        f"Workflow: {WORKFLOW_LABELS.get(workflow, workflow.title())}",
        "",
    ]
    if not events:
        lines.append("○ Choose a workflow to begin.")
    else:
        for index, event in enumerate(events):
            stage = str(event["stage"])
            marker = "✓" if index < len(events) - 1 or stage in {
                "completed", "facebook_done", "instagram_done", "preview"
            } else "●"
            if stage == "failed":
                marker = "!"
            label = PROGRESS_STAGE_LABELS.get(stage, stage.replace("_", " ").title())
            detail = str(event["detail"] or "").strip()
            line = f"{marker} {label}"
            if detail:
                line += f": {detail}"
            lines.append(line)
    lines.extend(
        [
            "",
            "Progress updates appear here. Approval only works by replying to the latest preview package.",
        ]
    )
    return "\n".join(lines)[-4096:]


def update_progress_message(
    session: requests.Session,
    config: Config,
    connection: sqlite3.Connection,
    job_id: int,
) -> None:
    job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None or not job["progress_message_id"]:
        return
    try:
        edit_text(
            session,
            config,
            int(job["progress_message_id"]),
            render_progress(connection, job_id),
        )
    except RuntimeError as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def report_progress(
    session: requests.Session,
    config: Config,
    connection: sqlite3.Connection,
    job_id: int,
    stage: str,
    detail: str | None = None,
) -> None:
    add_progress_event(connection, job_id, stage, detail)
    update_progress_message(session, config, connection, job_id)


def notify_failure(
    session: requests.Session,
    config: Config,
    job_id: int,
    reply_to_message_id: int,
    error: str,
) -> None:
    try:
        send_text(
            session,
            config,
            f"Watcher job {job_id} stopped safely. {error}. It will not retry automatically.",
            reply_to_message_id=reply_to_message_id,
        )
    except RuntimeError as exc:
        print(f"Could not send Telegram failure notice for job {job_id}: {exc}", file=sys.stderr)


def claim_next_job(connection: sqlite3.Connection) -> sqlite3.Row | None:
    connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        """
        SELECT * FROM jobs
        WHERE status = 'queued' AND workflow_type IS NOT NULL
        ORDER BY source_update_id LIMIT 1
        """
    ).fetchone()
    if row is None:
        connection.commit()
        return None
    connection.execute(
        """
        UPDATE jobs
        SET status = 'generating', turn_count = turn_count + 1,
            started_at = COALESCE(started_at, ?), updated_at = ?
        WHERE id = ?
        """,
        (utc_now(), utc_now(), row["id"]),
    )
    connection.commit()
    return connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()


def process_next_job(
    session: requests.Session, config: Config, connection: sqlite3.Connection
) -> bool:
    job = claim_next_job(connection)
    if job is None:
        return False
    result = invoke_codex(
        config,
        job,
        prompt=build_initial_prompt(
            int(job["id"]),
            str(job["request_text"]),
            str(job["workflow_type"]),
        ),
        reply_to_message_id=int(job["source_message_id"]),
        resume=False,
    )
    session_id = result.session_id
    if result.exit_code != 0 or not session_id or not result.receipt_path.is_file():
        error = result.error or (
            "Codex did not emit a persistent session ID"
            if not session_id
            else "Codex finished without delivering a Telegram preview"
        )
        mark_failed(connection, int(job["id"]), error)
        try:
            report_progress(
                session, config, connection, int(job["id"]), "failed", error
            )
        except RuntimeError:
            pass
        notify_failure(
            session,
            config,
            int(job["id"]),
            int(job["source_message_id"]),
            error,
        )
        return True

    connection.execute(
        "UPDATE jobs SET session_id = ?, updated_at = ? WHERE id = ?",
        (session_id, utc_now(), job["id"]),
    )
    connection.commit()
    try:
        preview_ids = register_preview(
            connection, int(job["id"]), int(job["turn_count"]), result.receipt_path
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        error = f"Could not register the Telegram preview receipt: {exc}"
        mark_failed(connection, int(job["id"]), error)
        notify_failure(
            session, config, int(job["id"]), int(job["source_message_id"]), error
        )
        return True
    try:
        report_progress(
            session,
            config,
            connection,
            int(job["id"]),
            "preview",
            f"{len(preview_ids)} Telegram messages",
        )
    except RuntimeError as exc:
        print(f"Could not update progress for job {job['id']}: {exc}", file=sys.stderr)
    try:
        instruction_ids = send_text(
            session,
            config,
            (
                f"Preview ready for watcher job {job['id']}. Reply exactly yes to this "
                "message or any message in the latest preview package to publish. "
                "Reply with changes instead to generate a revised preview."
            ),
            reply_to_message_id=preview_ids[-1],
        )
    except RuntimeError as exc:
        print(f"Could not send approval instructions for job {job['id']}: {exc}", file=sys.stderr)
        instruction_ids = []
    for message_id in instruction_ids:
        add_active_preview_message(
            connection, int(job["id"]), int(job["turn_count"]), message_id
        )
    return True


def preview_job_for_reply(
    connection: sqlite3.Connection, reply_to_message_id: int
) -> tuple[sqlite3.Row | None, bool]:
    mapping = connection.execute(
        "SELECT job_id, active FROM preview_messages WHERE message_id = ?",
        (reply_to_message_id,),
    ).fetchone()
    if mapping is None:
        return None, False
    job = connection.execute(
        "SELECT * FROM jobs WHERE id = ?", (mapping["job_id"],)
    ).fetchone()
    return job, bool(mapping["active"])


def record_event(
    connection: sqlite3.Connection,
    message: TelegramMessage | TelegramCallback,
    kind: str,
    job_id: int | None,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO telegram_events(
            update_id, message_id, job_id, kind, processed_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (message.update_id, message.message_id, job_id, kind, utc_now()),
    )
    set_setting(connection, "telegram_offset", str(message.update_id + 1))
    connection.commit()


def enqueue_request(
    connection: sqlite3.Connection,
    message: TelegramMessage,
    *,
    workflow_type: str | None = None,
) -> int:
    if workflow_type is not None and workflow_type not in WORKFLOW_TYPES:
        raise ValueError(f"Unsupported workflow type: {workflow_type}")
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO jobs(
            source_update_id, chat_id, source_message_id, sender_id,
            request_text, received_at, updated_at, workflow_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message.update_id,
            message.chat_id,
            message.message_id,
            message.sender_id,
            message.text,
            message.received_at,
            utc_now(),
            workflow_type,
        ),
    )
    if cursor.rowcount == 1:
        job_id = int(cursor.lastrowid)
    else:
        row = connection.execute(
            "SELECT id FROM jobs WHERE source_update_id = ?", (message.update_id,)
        ).fetchone()
        job_id = int(row["id"])
    record_event(connection, message, "queued", job_id)
    return job_id


def set_progress_message(
    connection: sqlite3.Connection, job_id: int, message_id: int
) -> None:
    connection.execute(
        """
        UPDATE jobs
        SET selector_message_id = ?, progress_message_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (message_id, message_id, utc_now(), job_id),
    )
    connection.commit()


def select_workflow(
    connection: sqlite3.Connection, job_id: int, workflow_type: str
) -> bool:
    if workflow_type not in WORKFLOW_TYPES:
        raise ValueError(f"Unsupported workflow type: {workflow_type}")
    cursor = connection.execute(
        """
        UPDATE jobs SET workflow_type = ?, updated_at = ?
        WHERE id = ? AND status = 'queued' AND workflow_type IS NULL
        """,
        (workflow_type, utc_now(), job_id),
    )
    connection.commit()
    return cursor.rowcount == 1


def cancel_pending_job(connection: sqlite3.Connection, job_id: int) -> bool:
    cursor = connection.execute(
        """
        UPDATE jobs
        SET status = 'failed', updated_at = ?, finished_at = ?,
            last_error = 'Cancelled before workflow selection'
        WHERE id = ? AND status = 'queued' AND workflow_type IS NULL
        """,
        (utc_now(), utc_now(), job_id),
    )
    connection.commit()
    return cursor.rowcount == 1


def progress_job_for_reply(
    connection: sqlite3.Connection, message_id: int
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM jobs WHERE progress_message_id = ?", (message_id,)
    ).fetchone()


def claim_resume_turn(
    connection: sqlite3.Connection,
    job_id: int,
    *,
    approval: bool,
) -> sqlite3.Row:
    status = "publishing" if approval else "revising"
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        """
        UPDATE jobs
        SET status = ?, turn_count = turn_count + 1, updated_at = ?
        WHERE id = ? AND status = 'awaiting_approval'
        """,
        (status, utc_now(), job_id),
    )
    connection.commit()
    return connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def process_resume(
    session: requests.Session,
    config: Config,
    connection: sqlite3.Connection,
    job: sqlite3.Row,
    message: TelegramMessage,
    *,
    approval: bool,
) -> None:
    claimed = claim_resume_turn(connection, int(job["id"]), approval=approval)
    try:
        report_progress(
            session,
            config,
            connection,
            int(job["id"]),
            "publishing_facebook" if approval else "revision",
            None,
        )
    except RuntimeError as exc:
        print(f"Could not update progress for job {job['id']}: {exc}", file=sys.stderr)
    result = invoke_codex(
        config,
        claimed,
        prompt="yes" if approval else build_revision_prompt(message.text),
        reply_to_message_id=message.message_id,
        resume=True,
    )
    if result.exit_code != 0:
        error = result.error or "Codex resume failed"
        mark_failed(connection, int(job["id"]), error)
        try:
            report_progress(
                session, config, connection, int(job["id"]), "failed", error
            )
        except RuntimeError:
            pass
        notify_failure(session, config, int(job["id"]), message.message_id, error)
        return

    if result.receipt_path.is_file():
        try:
            preview_ids = register_preview(
                connection,
                int(job["id"]),
                int(claimed["turn_count"]),
                result.receipt_path,
            )
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            error = f"Could not register the revised Telegram preview receipt: {exc}"
            mark_failed(connection, int(job["id"]), error)
            notify_failure(session, config, int(job["id"]), message.message_id, error)
            return
        try:
            instruction_ids = send_text(
                session,
                config,
                (
                    f"Revised preview ready for watcher job {job['id']}. Reply exactly "
                    "yes to the latest package to publish, or reply with more changes."
                ),
                reply_to_message_id=preview_ids[-1],
            )
        except RuntimeError as exc:
            print(
                f"Could not send revised approval instructions for job {job['id']}: {exc}",
                file=sys.stderr,
            )
            instruction_ids = []
        for message_id in instruction_ids:
            add_active_preview_message(
                connection,
                int(job["id"]),
                int(claimed["turn_count"]),
                message_id,
            )
        try:
            report_progress(
                session,
                config,
                connection,
                int(job["id"]),
                "preview",
                f"{len(preview_ids)} Telegram messages",
            )
        except RuntimeError:
            pass
        return

    if not approval:
        error = "Codex accepted the revision but did not deliver a new Telegram preview"
        mark_failed(connection, int(job["id"]), error)
        notify_failure(session, config, int(job["id"]), message.message_id, error)
        return

    final_output = ""
    if result.final_output_path.is_file():
        try:
            final_output = result.final_output_path.read_text(
                encoding="utf-8", errors="replace"
            ).strip()
        except OSError as exc:
            print(f"Could not read final output for job {job['id']}: {exc}", file=sys.stderr)
    connection.execute(
        """
        UPDATE jobs
        SET status = 'completed', updated_at = ?, finished_at = ?,
            final_output = ?, last_error = NULL
        WHERE id = ?
        """,
        (utc_now(), utc_now(), final_output, job["id"]),
    )
    connection.execute(
        "UPDATE preview_messages SET active = 0 WHERE job_id = ?", (job["id"],)
    )
    connection.commit()
    try:
        report_progress(
            session, config, connection, int(job["id"]), "completed", None
        )
    except RuntimeError as exc:
        print(f"Could not update progress for job {job['id']}: {exc}", file=sys.stderr)
    try:
        send_text(
            session,
            config,
            final_output or f"Watcher job {job['id']} completed successfully.",
            reply_to_message_id=message.message_id,
        )
    except RuntimeError as exc:
        print(f"Could not send completion notice for job {job['id']}: {exc}", file=sys.stderr)


def handle_update(
    session: requests.Session,
    config: Config,
    connection: sqlite3.Connection,
    update: dict[str, Any],
) -> None:
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return
    if connection.execute(
        "SELECT 1 FROM telegram_events WHERE update_id = ?", (update_id,)
    ).fetchone():
        set_setting(connection, "telegram_offset", str(update_id + 1))
        connection.commit()
        return
    callback = parse_callback(update, config)
    if callback is not None:
        match = re.fullmatch(
            r"workflow:(\d+):(news|model|product|reel|auto|cancel)",
            callback.data,
        )
        if match is None:
            record_event(connection, callback, "ignored_callback", None)
            answer_callback(session, config, callback.callback_id, "This button is not recognized.")
            return
        job_id = int(match.group(1))
        choice = match.group(2)
        job = connection.execute(
            "SELECT * FROM jobs WHERE id = ? AND selector_message_id = ?",
            (job_id, callback.message_id),
        ).fetchone()
        if job is None:
            record_event(connection, callback, "ignored_stale_callback", None)
            answer_callback(session, config, callback.callback_id, "This selector is no longer active.")
            return
        if choice == "cancel":
            changed = cancel_pending_job(connection, job_id)
            record_event(connection, callback, "cancelled" if changed else "ignored_stale_callback", job_id)
            answer_callback(
                session,
                config,
                callback.callback_id,
                "Cancelled." if changed else "This job has already started.",
            )
            if changed:
                edit_text(
                    session,
                    config,
                    callback.message_id,
                    f"Bits Today · Job {job_id}\n\n! Cancelled before workflow selection.",
                )
            return
        changed = select_workflow(connection, job_id, choice)
        record_event(
            connection,
            callback,
            "workflow_selected" if changed else "ignored_stale_callback",
            job_id,
        )
        answer_callback(
            session,
            config,
            callback.callback_id,
            (
                f"{WORKFLOW_LABELS[choice]} selected."
                if changed
                else "This job has already been selected."
            ),
        )
        if changed:
            report_progress(
                session,
                config,
                connection,
                job_id,
                "selected",
                WORKFLOW_LABELS[choice],
            )
        return
    message = parse_message(update, config)
    if message is None:
        set_setting(connection, "telegram_offset", str(update_id + 1))
        connection.commit()
        return
    if message.text.startswith("/"):
        command_match = re.match(
            r"^/(news|model|product|reel|auto)(?:@\w+)?\s+(.+)$",
            message.text,
            re.IGNORECASE | re.DOTALL,
        )
        if command_match is None:
            record_event(connection, message, "ignored_command", None)
            send_text(
                session,
                config,
                "Send an X status URL, or use /news, /model, /product, /reel, "
                "or /auto followed by the URL.",
                reply_to_message_id=message.message_id,
            )
            return
        workflow_type = command_match.group(1).lower()
        request_text = command_match.group(2).strip()
        if X_STATUS_URL_RE.search(request_text) is None:
            record_event(connection, message, "invalid_request", None)
            send_text(
                session,
                config,
                "I could not find an X/Twitter status URL in that command.",
                reply_to_message_id=message.message_id,
            )
            return
        direct_message = TelegramMessage(
            update_id=message.update_id,
            message_id=message.message_id,
            chat_id=message.chat_id,
            sender_id=message.sender_id,
            text=request_text,
            reply_to_message_id=message.reply_to_message_id,
            received_at=message.received_at,
        )
        job_id = enqueue_request(
            connection, direct_message, workflow_type=workflow_type
        )
        try:
            ids = send_text(
                session,
                config,
                f"Bits Today · Job {job_id}\nWorkflow: {WORKFLOW_LABELS[workflow_type]}\n\n● Queued",
                reply_to_message_id=message.message_id,
            )
        except RuntimeError as exc:
            mark_failed(connection, job_id, f"Could not create progress dashboard: {exc}")
            return
        if ids:
            set_progress_message(connection, job_id, ids[0])
            report_progress(
                session,
                config,
                connection,
                job_id,
                "selected",
                WORKFLOW_LABELS[workflow_type],
            )
        return

    job = None
    active = False
    if message.reply_to_message_id is not None:
        job, active = preview_job_for_reply(connection, message.reply_to_message_id)
    if job is not None:
        if not active or job["status"] != "awaiting_approval":
            record_event(connection, message, "ignored_stale_reply", int(job["id"]))
            send_text(
                session,
                config,
                "That preview is no longer current. Reply to the latest preview package.",
                reply_to_message_id=message.message_id,
            )
            return
        approval = message.text == "yes"
        record_event(
            connection,
            message,
            "approval" if approval else "revision",
            int(job["id"]),
        )
        process_resume(
            session,
            config,
            connection,
            job,
            message,
            approval=approval,
        )
        return

    if message.reply_to_message_id is not None:
        progress_job = progress_job_for_reply(
            connection, message.reply_to_message_id
        )
        if progress_job is not None:
            record_event(
                connection,
                message,
                "ignored_progress_reply",
                int(progress_job["id"]),
            )
            send_text(
                session,
                config,
                "That message only shows progress. Reply to the latest preview package for revisions or approval.",
                reply_to_message_id=message.message_id,
            )
            return

    if message.text == "yes":
        record_event(connection, message, "ignored_unthreaded_approval", None)
        send_text(
            session,
            config,
            "Approval was not applied. Reply exactly yes to the latest preview message.",
            reply_to_message_id=message.message_id,
        )
        return
    if X_STATUS_URL_RE.search(message.text) is None:
        record_event(connection, message, "invalid_request", None)
        send_text(
            session,
            config,
            "Send an X/Twitter status URL to start a Bits Today post.",
            reply_to_message_id=message.message_id,
        )
        return
    job_id = enqueue_request(connection, message)
    try:
        selector_ids = send_text(
            session,
            config,
            (
                f"Bits Today · Job {job_id}\n\n"
                "Choose the workflow for this X post. Auto Detect uses the existing "
                "news/model/product classifier."
            ),
            reply_to_message_id=message.message_id,
            reply_markup=workflow_keyboard(job_id),
        )
    except RuntimeError as exc:
        mark_failed(connection, job_id, f"Could not send workflow selector: {exc}")
        return
    if not selector_ids:
        mark_failed(connection, job_id, "Telegram returned no workflow selector message")
        return
    set_progress_message(connection, job_id, selector_ids[0])


def queue_status(connection: sqlite3.Connection) -> dict[str, Any]:
    counts = {
        row["status"]: int(row["count"])
        for row in connection.execute(
            "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
        )
    }
    jobs = [
        dict(row)
        for row in connection.execute(
            """
            SELECT id, source_message_id, workflow_type, progress_message_id,
                   status, session_id, turn_count,
                   received_at, updated_at, finished_at, last_error
            FROM jobs ORDER BY id DESC LIMIT 20
            """
        )
    ]
    return {
        "telegram_offset": get_setting(connection, "telegram_offset"),
        "counts": counts,
        "recent_jobs": jobs,
    }


def without_managed_cron_block(crontab_text: str) -> str:
    lines: list[str] = []
    inside = False
    for line in crontab_text.splitlines():
        if line.strip() == CRON_BEGIN:
            inside = True
            continue
        if line.strip() == CRON_END:
            inside = False
            continue
        if not inside:
            lines.append(line)
    return "\n".join(lines).strip()


def pause_managed_cron() -> bool:
    current = subprocess.run(
        ["/usr/bin/crontab", "-l"], text=True, capture_output=True, check=False
    )
    if current.returncode == 1:
        return False
    if current.returncode != 0:
        raise RuntimeError(current.stderr.strip() or "Could not read root crontab")
    cleaned = without_managed_cron_block(current.stdout)
    changed = cleaned.strip() != current.stdout.strip()
    if changed:
        result = subprocess.run(
            ["/usr/bin/crontab", "-"],
            input=(cleaned + "\n") if cleaned else "",
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Could not update root crontab")
    return changed


@contextmanager
def watcher_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+", encoding="utf-8") as lock_file:
        path.chmod(0o600)
        if fcntl is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "Another Telegram watcher process is already running"
                ) from exc
            yield
            return

        if msvcrt is None:
            raise RuntimeError("No supported process-locking implementation is available")
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write("\0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError(
                "Another Telegram watcher process is already running"
            ) from exc
        try:
            yield
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def watch(config: Config, connection: sqlite3.Connection) -> None:
    migrate_queue_offset(config, connection)
    interrupted = fail_interrupted_jobs(connection)
    if interrupted:
        print(json.dumps({"interrupted_jobs_marked_failed": interrupted}), flush=True)
    with requests.Session() as session:
        while True:
            while process_next_job(session, config, connection):
                pass
            offset = int(get_setting(connection, "telegram_offset") or "0")
            try:
                updates = get_updates(session, config, offset)
                for update in sorted(updates, key=lambda item: int(item.get("update_id", -1))):
                    handle_update(session, config, connection, update)
            except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
                print(f"Watcher error: {exc}", file=sys.stderr, flush=True)
                time.sleep(5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--watch", action="store_true", help="run the long-polling watcher")
    action.add_argument("--initialize", action="store_true", help="initialize state and migrate the cron queue offset")
    action.add_argument("--status", action="store_true", help="print watcher status")
    action.add_argument("--pause-cron", action="store_true", help="remove only the managed hourly cron block")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.pause_cron:
        print(json.dumps({"managed_cron_paused": pause_managed_cron()}))
        return 0
    config = load_config()
    connection = connect_database(config.database_path)
    try:
        if args.initialize:
            offset = migrate_queue_offset(config, connection)
            print(json.dumps({"initialized": True, "telegram_offset": offset}))
            return 0
        if args.status:
            migrate_queue_offset(config, connection)
            print(json.dumps(queue_status(connection), ensure_ascii=False, indent=2))
            return 0
        with watcher_lock(config.state_dir / "watcher.lock"):
            watch(config, connection)
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
