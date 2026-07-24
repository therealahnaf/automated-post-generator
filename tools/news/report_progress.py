#!/usr/bin/env python3
"""Record a watcher milestone and edit its single Telegram progress message."""

from __future__ import annotations

import argparse
import json
import os
import sys

import requests

try:
    from .telegram_codex_watcher import (
        PROGRESS_STAGE_LABELS,
        connect_database,
        load_config,
        report_progress,
    )
except ImportError:
    from telegram_codex_watcher import (
        PROGRESS_STAGE_LABELS,
        connect_database,
        load_config,
        report_progress,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--job-id",
        type=int,
        default=None,
        help="Watcher job ID; defaults to TELEGRAM_WATCHER_JOB_ID.",
    )
    parser.add_argument("--stage", required=True, choices=tuple(PROGRESS_STAGE_LABELS))
    parser.add_argument(
        "--detail",
        help="Short user-facing detail, such as the headline or source count.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raw_job_id = str(args.job_id or os.getenv("TELEGRAM_WATCHER_JOB_ID", "")).strip()
    if not raw_job_id.isdigit() or int(raw_job_id) <= 0:
        print("Error: a positive --job-id or TELEGRAM_WATCHER_JOB_ID is required.", file=sys.stderr)
        return 1
    detail = (args.detail or "").strip()
    if len(detail) > 500:
        print("Error: --detail cannot exceed 500 characters.", file=sys.stderr)
        return 1
    try:
        config = load_config()
        connection = connect_database(config.database_path)
        try:
            with requests.Session() as session:
                report_progress(
                    session,
                    config,
                    connection,
                    int(raw_job_id),
                    args.stage,
                    detail or None,
                )
        finally:
            connection.close()
        print(
            json.dumps(
                {
                    "updated": True,
                    "job_id": int(raw_job_id),
                    "stage": args.stage,
                    "detail": detail or None,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
