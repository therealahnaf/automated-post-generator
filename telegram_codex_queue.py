#!/usr/bin/env python3
"""Poll a private Telegram chat and run one queued Codex job at a time."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from dotenv import load_dotenv


REPO_DIR = Path(__file__).resolve().parent
PROMPT_PREFIX = "Read AGENTS.md"
PROMPT_SUFFIX = "NO NEED TO SEND PREVIEW. AUTOMATICALLY POST THE GENERATED POST"
CRON_BEGIN = "# BEGIN bits-today telegram codex queue"
CRON_END = "# END bits-today telegram codex queue"


@dataclass(frozen=True)
class Config:
    telegram_token: str
    telegram_chat_id: str
    allowed_user_ids: frozenset[str]
    repo_dir: Path
    state_dir: Path
    database_path: Path
    codex_bin: Path
    python_bin: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config() -> Config:
    load_dotenv(REPO_DIR / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")

    repo_dir = Path(os.environ.get("CODEX_QUEUE_REPO", REPO_DIR)).resolve()
    state_dir = Path(
        os.environ.get("CODEX_QUEUE_STATE_DIR", repo_dir / ".automation")
    ).resolve()
    database_path = Path(
        os.environ.get("CODEX_QUEUE_DB", state_dir / "telegram_codex_queue.sqlite3")
    ).resolve()
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
        # Keep the virtual-environment path; resolving its symlink would make
        # cron bypass the venv and lose the installed Python dependencies.
        python_bin=Path(sys.executable).absolute(),
    )


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    connection = sqlite3.connect(path, timeout=30)
    path.chmod(0o600)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            update_id INTEGER NOT NULL UNIQUE,
            chat_id TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            sender_id TEXT,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'completed', 'failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            received_at TEXT,
            started_at TEXT,
            finished_at TEXT,
            codex_exit_code INTEGER,
            last_error TEXT,
            stdout_path TEXT,
            stderr_path TEXT,
            final_output_path TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(chat_id, message_id)
        );

        CREATE INDEX IF NOT EXISTS jobs_update_fifo_idx ON jobs(status, update_id);
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


def telegram_get_updates(
    session: requests.Session,
    token: str,
    offset: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
    response = session.post(
        f"https://api.telegram.org/bot{token}/getUpdates",
        json={
            "offset": offset,
            "limit": limit,
            "timeout": 0,
            "allowed_updates": ["message"],
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        description = payload.get("description", "unknown error")
        raise RuntimeError(f"Telegram getUpdates failed: {description}")
    result = payload.get("result")
    if not isinstance(result, list):
        raise RuntimeError("Telegram getUpdates returned an invalid result")
    return result


def message_from_update(
    update: dict[str, Any],
    expected_chat_id: str,
    allowed_user_ids: frozenset[str],
) -> tuple[int, str, int, str | None, str, str | None] | None:
    update_id = update.get("update_id")
    message = update.get("message")
    if not isinstance(update_id, int) or not isinstance(message, dict):
        return None
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, dict) or str(chat.get("id")) != expected_chat_id:
        return None
    if isinstance(sender, dict) and sender.get("is_bot"):
        return None
    sender_id = str(sender.get("id")) if isinstance(sender, dict) and sender.get("id") else None
    if allowed_user_ids and sender_id not in allowed_user_ids:
        return None
    message_id = message.get("message_id")
    text = message.get("text")
    if not isinstance(message_id, int) or not isinstance(text, str):
        return None
    text = text.strip()
    if not text or text.startswith("/"):
        return None
    received_at = None
    if isinstance(message.get("date"), int):
        received_at = datetime.fromtimestamp(message["date"], timezone.utc).isoformat(
            timespec="seconds"
        )
    return update_id, expected_chat_id, message_id, sender_id, text, received_at


def enqueue_updates(
    connection: sqlite3.Connection,
    updates: Iterable[dict[str, Any]],
    expected_chat_id: str,
    allowed_user_ids: frozenset[str],
) -> int:
    inserted = 0
    now = utc_now()
    for update in updates:
        item = message_from_update(update, expected_chat_id, allowed_user_ids)
        if item is None:
            continue
        update_id, chat_id, message_id, sender_id, text, received_at = item
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO jobs(
                update_id, chat_id, message_id, sender_id, text, received_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (update_id, chat_id, message_id, sender_id, text, received_at, now),
        )
        inserted += cursor.rowcount
    return inserted


def bootstrap(config: Config, connection: sqlite3.Connection) -> int:
    """Forget existing Telegram history so installation never republishes old messages."""
    with requests.Session() as session:
        updates = telegram_get_updates(session, config.telegram_token, offset=-1, limit=1)
    next_offset = max((int(update["update_id"]) for update in updates), default=-1) + 1
    set_setting(connection, "telegram_offset", str(next_offset))
    set_setting(connection, "bootstrapped_at", utc_now())
    connection.commit()
    return next_offset


def poll(config: Config, connection: sqlite3.Connection) -> int:
    raw_offset = get_setting(connection, "telegram_offset")
    if raw_offset is None:
        bootstrap(config, connection)
        return 0

    offset = int(raw_offset)
    total_inserted = 0
    with requests.Session() as session:
        while True:
            updates = telegram_get_updates(session, config.telegram_token, offset=offset)
            if not updates:
                break
            total_inserted += enqueue_updates(
                connection,
                updates,
                config.telegram_chat_id,
                config.allowed_user_ids,
            )
            offset = max(int(update["update_id"]) for update in updates) + 1
            set_setting(connection, "telegram_offset", str(offset))
            connection.commit()
            if len(updates) < 100:
                break
    return total_inserted


def build_prompt(message: str) -> str:
    return f"{PROMPT_PREFIX}\n\n{message.strip()}\n\n{PROMPT_SUFFIX}"


def fail_stale_jobs(connection: sqlite3.Connection, hours: int = 6) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    cursor = connection.execute(
        """
        UPDATE jobs
        SET status = 'failed', finished_at = ?,
            last_error = 'Marked failed after the runner stopped unexpectedly; manual retry required'
        WHERE status = 'running' AND started_at < ?
        """,
        (utc_now(), cutoff),
    )
    connection.commit()
    return cursor.rowcount


def claim_next_job(connection: sqlite3.Connection) -> sqlite3.Row | None:
    connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        "SELECT * FROM jobs WHERE status = 'queued' ORDER BY update_id LIMIT 1"
    ).fetchone()
    if row is None:
        connection.commit()
        return None
    connection.execute(
        """
        UPDATE jobs
        SET status = 'running', attempts = attempts + 1, started_at = ?,
            finished_at = NULL, codex_exit_code = NULL, last_error = NULL
        WHERE id = ?
        """,
        (utc_now(), row["id"]),
    )
    connection.commit()
    return connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()


def execute_next_job(config: Config, connection: sqlite3.Connection) -> int | None:
    job = claim_next_job(connection)
    if job is None:
        return None

    logs_dir = config.state_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stem = f"job-{job['id']}-attempt-{job['attempts']}"
    stdout_path = logs_dir / f"{stem}.jsonl"
    stderr_path = logs_dir / f"{stem}.stderr.log"
    final_output_path = logs_dir / f"{stem}.final.txt"
    command = [
        str(config.codex_bin),
        "--search",
        "--ask-for-approval",
        "never",
        "exec",
        "--sandbox",
        "danger-full-access",
        "--ephemeral",
        "--json",
        "--cd",
        str(config.repo_dir),
        "--output-last-message",
        str(final_output_path),
        "-",
    ]

    try:
        with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr_file:
            result = subprocess.run(
                command,
                input=build_prompt(str(job["text"])),
                text=True,
                cwd=config.repo_dir,
                stdout=stdout_file,
                stderr=stderr_file,
                check=False,
            )
        exit_code = result.returncode
        status = "completed" if exit_code == 0 else "failed"
        error = (
            None
            if exit_code == 0
            else f"Codex exited with status {exit_code}; manual retry required"
        )
    except Exception as exc:
        exit_code = -1
        status = "failed"
        error = f"Could not run Codex: {type(exc).__name__}: {exc}"

    connection.execute(
        """
        UPDATE jobs
        SET status = ?, finished_at = ?, codex_exit_code = ?, last_error = ?,
            stdout_path = ?, stderr_path = ?, final_output_path = ?
        WHERE id = ?
        """,
        (
            status,
            utc_now(),
            exit_code,
            error,
            str(stdout_path),
            str(stderr_path),
            str(final_output_path),
            job["id"],
        ),
    )
    connection.commit()
    return exit_code


def retry_job(connection: sqlite3.Connection, job_id: int) -> bool:
    cursor = connection.execute(
        """
        UPDATE jobs
        SET status = 'queued', started_at = NULL, finished_at = NULL,
            codex_exit_code = NULL, last_error = NULL
        WHERE id = ? AND status = 'failed'
        """,
        (job_id,),
    )
    connection.commit()
    return cursor.rowcount == 1


def queue_status(connection: sqlite3.Connection) -> dict[str, Any]:
    counts = {
        row["status"]: row["count"]
        for row in connection.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status")
    }
    oldest = connection.execute(
        """
        SELECT id, received_at, created_at
        FROM jobs WHERE status = 'queued' ORDER BY update_id LIMIT 1
        """
    ).fetchone()
    return {
        "telegram_offset": get_setting(connection, "telegram_offset"),
        "counts": {
            name: int(counts.get(name, 0))
            for name in ("queued", "running", "completed", "failed")
        },
        "oldest_queued": None if oldest is None else dict(oldest),
    }


def without_managed_cron_block(crontab_text: str) -> str:
    output: list[str] = []
    inside = False
    for line in crontab_text.splitlines():
        if line.strip() == CRON_BEGIN:
            inside = True
            continue
        if line.strip() == CRON_END:
            inside = False
            continue
        if not inside:
            output.append(line)
    return "\n".join(output).strip()


def install_cron(config: Config) -> str:
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    minute_text = os.environ.get("CODEX_QUEUE_CRON_MINUTE", "7").strip()
    if not minute_text.isdigit() or not 0 <= int(minute_text) <= 59:
        raise RuntimeError("CODEX_QUEUE_CRON_MINUTE must be an integer from 0 through 59")

    current = subprocess.run(
        ["/usr/bin/crontab", "-l"], text=True, capture_output=True, check=False
    )
    if current.returncode not in (0, 1):
        raise RuntimeError(current.stderr.strip() or "Could not read the current crontab")
    existing = without_managed_cron_block(current.stdout if current.returncode == 0 else "")

    lock_path = config.state_dir / "telegram_codex_queue.lock"
    lock_path.touch(mode=0o600, exist_ok=True)
    lock_path.chmod(0o600)
    cron_log = config.state_dir / "cron.log"
    command_parts = [
        "/usr/bin/flock",
        "-n",
        str(lock_path),
        str(config.python_bin),
        str(REPO_DIR / "telegram_codex_queue.py"),
        "--run-once",
    ]
    command = " ".join(shlex.quote(part) for part in command_parts)
    cron_line = (
        f"{int(minute_text)} * * * * umask 077; {command} "
        f">> {shlex.quote(str(cron_log))} 2>&1"
    )
    sections = [part for part in (existing, CRON_BEGIN, cron_line, CRON_END) if part]
    new_crontab = "\n".join(sections) + "\n"
    installed = subprocess.run(
        ["/usr/bin/crontab", "-"],
        input=new_crontab,
        text=True,
        capture_output=True,
        check=False,
    )
    if installed.returncode != 0:
        raise RuntimeError(installed.stderr.strip() or "Could not install the crontab")
    return cron_line


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--run-once", action="store_true", help="poll Telegram and run one FIFO job"
    )
    action.add_argument("--bootstrap", action="store_true", help="skip existing Telegram history")
    action.add_argument(
        "--install-cron",
        action="store_true",
        help="bootstrap and install the hourly cron entry",
    )
    action.add_argument("--status", action="store_true", help="print queue status as JSON")
    action.add_argument(
        "--retry", type=int, metavar="JOB_ID", help="put one failed job back in the queue"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config()
    connection = connect_database(config.database_path)
    try:
        if args.bootstrap:
            offset = bootstrap(config, connection)
            print(json.dumps({"bootstrapped": True, "telegram_offset": offset}))
            return 0
        if args.install_cron:
            offset = bootstrap(config, connection)
            cron_line = install_cron(config)
            print(
                json.dumps(
                    {"bootstrapped": True, "telegram_offset": offset, "cron": cron_line}
                )
            )
            return 0
        if args.status:
            print(json.dumps(queue_status(connection), indent=2))
            return 0
        if args.retry is not None:
            if not retry_job(connection, args.retry):
                print(f"Job {args.retry} was not found in failed state", file=sys.stderr)
                return 1
            print(f"Job {args.retry} queued for manual retry")
            return 0

        fail_stale_jobs(connection)
        inserted = poll(config, connection)
        exit_code = execute_next_job(config, connection)
        print(
            json.dumps(
                {"new_jobs": inserted, "codex_exit_code": exit_code, **queue_status(connection)}
            )
        )
        return 0 if exit_code in (None, 0) else 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
