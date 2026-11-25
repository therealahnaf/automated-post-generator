"""Local dev runs of the agent workflow: previews and progress go to files, and nothing is sent or published.

The dev runner (`tools/dev/agent.py`) sets BITS_TODAY_DEV_DIR for the Codex session. While it is set:

* `notify_telegram.py` saves the preview package (images, video, caption, receipt) under that directory instead of
  sending it to Telegram;
* `report_progress.py` appends milestones to `progress.jsonl` there instead of editing the Telegram dashboard;
* every publisher and website-archive command refuses to run.

Without the variable, production behaves exactly as before.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ENV = "BITS_TODAY_DEV_DIR"


def directory() -> Path | None:
    value = os.getenv(ENV, "").strip()
    return Path(value).resolve() if value else None


def refuse(action: str) -> int:
    """Stop a command that would reach Facebook, Instagram, YouTube or the website from a dev run."""
    print(f"Error: dev mode ({ENV} is set): {action} is disabled. Nothing was sent or published.", file=sys.stderr)
    return 2


def save_preview(*, images: list[Path], videos: list[Path], description: str, platform: str | None,
                 stage: str) -> dict:
    """Copy one preview package into <dev dir>/previews/<n>-<platform>/ and return a Telegram-shaped result."""
    root = directory()
    assert root is not None
    previews = root / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    number = 1 + sum(1 for item in previews.iterdir() if item.is_dir())
    folder = previews / f"{number:02d}-{platform or 'default'}-{stage}"
    folder.mkdir()
    copied = []
    for index, source in enumerate([*images, *videos], start=1):
        target = folder / f"{index:02d}{source.suffix.lower()}"
        shutil.copy2(source, target)
        copied.append(str(target))
    (folder / "caption.txt").write_text(description, encoding="utf-8")
    (folder / "package.json").write_text(json.dumps({
        "stage": stage, "platform": platform, "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": [str(path.resolve()) for path in [*images, *videos]], "copies": copied,
    }, indent=2), encoding="utf-8")
    return {
        "reply_to_message_id": None,
        "photo_message_ids": [],
        "video_message_ids": [],
        "description_message_ids": [],
        "dev_preview_dir": str(folder),
    }


def record_progress(job_id: int, stage: str, detail: str | None) -> None:
    root = directory()
    assert root is not None
    root.mkdir(parents=True, exist_ok=True)
    with (root / "progress.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "job_id": job_id,
                              "stage": stage, "detail": detail}, ensure_ascii=False) + "\n")
