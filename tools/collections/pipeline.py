#!/usr/bin/env python3
"""Collection posts: one Instagram carousel built from a saved collection. No agent; every step is a script.

  start --collection <id> --type media|tweets|combo
                       title and caption -> cover and slides (photos and videos) -> Telegram album preview
  revise --feedback <text>
                       "title: ..." / "caption: ..." set that text exactly; "type: media|tweets|combo" switches the
                       type; "drop 3 5" / "move 4 to 1" change the collection (numbers as on its contact sheet);
                       anything else rewrites the title (and caption, if mentioned) with the feedback
  publish              after `yes`: Instagram only (collections skip Facebook and the website)

The job folder is $BITS_TODAY_JOB_DIR; the Telegram watcher runs these commands for Save and build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.collections import slides, store  # noqa: E402
from tools.news_v2.pipeline import StepError, final_output, last_json, log, progress, read_json, run  # noqa: E402

PY = sys.executable
WORKFLOW = "collection"


class Job:
    def __init__(self, folder: Path):
        self.dir = folder
        self.copy = folder / "copy.json"
        self.package = folder / "package"
        self.caption = folder / "captions" / "instagram-description.txt"
        self.state_path = folder / "collection-state.json"
        self.log = folder / "collection.log"

    def state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.is_file() else {}

    def save(self, **values) -> None:
        self.state_path.write_text(json.dumps({**self.state(), **values}, indent=2) + "\n", encoding="utf-8")


def job_from_env() -> Job:
    folder = os.getenv("BITS_TODAY_JOB_DIR", "").strip()
    if not folder:
        raise SystemExit("BITS_TODAY_JOB_DIR is not set.")
    Path(folder).mkdir(parents=True, exist_ok=True)
    return Job(Path(folder))


def write_copy(job: Job, collection_id: int, feedback: str = "") -> dict:
    command = [PY, "tools/collections/collection_copy.py", "--collection", str(collection_id), "--output", str(job.copy)]
    if feedback:
        command += ["--feedback", feedback]
    run(job, command, "title and caption")
    copy = read_json(job.copy)
    progress(job, "headline", copy["title"])
    return copy


def build(job: Job) -> list[Path]:
    """Render the cover and slides, write the caption, and send the album preview."""
    from tools.collections import collection_copy

    state = job.state()
    collection_id, kind = int(state["collection_id"]), state["type"]
    items = store.items(store.connect(), collection_id)
    if not items:
        raise StepError("The collection is empty.")
    copy = read_json(job.copy)
    progress(job, "generating_items", f"{slides.TYPE_LABELS[kind]} from {len(items)} posts")
    cover, planned, left_out = slides.plan(items, kind)
    if not planned:
        raise StepError("The collection needs at least two posts, or one post with more than one photo or video.")
    if job.package.is_dir():
        shutil.rmtree(job.package)
    job.package.mkdir(parents=True)
    seed = f"collection-{collection_id}"
    count = len(planned) + 1
    posted = datetime.now(timezone.utc).date()
    cover_path = job.package / "01.jpg"
    try:
        slides.render_cover(copy["title"], posted, cover, planned, job.package, seed).save(cover_path, quality=92)
        rendered = [cover_path] + [slides.render_slide(slide, index, count, job.package, seed)
                                   for index, slide in enumerate(planned, start=2)]
    except subprocess.CalledProcessError as exc:
        raise StepError(f"Rendering a video slide failed: {exc}") from exc
    job.caption.parent.mkdir(exist_ok=True)
    job.caption.write_text(collection_copy.caption_text(copy, items), encoding="utf-8")
    videos = sum(path.suffix == ".mp4" for path in rendered)
    detail = f"{len(rendered)} slides ({videos} video)" + (f"; posts {', '.join(map(str, left_out))} did not fit" if left_out else "")
    progress(job, "items_ready", detail)
    run(job, [PY, "tools/news/notify_telegram.py", *[flag for path in rendered for flag in ("--slide", str(path))],
              "--description-file", str(job.caption), "--stage", "preview", "--send", "--platform", "instagram"],
        "preview")
    progress(job, "preview", detail)
    job.save(title=copy["title"], slides=[str(path) for path in rendered], left_out=left_out)
    return rendered


def parse_request(text: str) -> tuple[int, str]:
    match = re.search(r"collection:(\d+):(media|tweets|combo)", text)
    if not match:
        raise StepError("The request does not name a collection and type.")
    return int(match.group(1)), match.group(2)


def command_start(args: argparse.Namespace) -> int:
    job = job_from_env()
    # The watcher keeps a session ID per job; collections have no agent session, so the job's own name stands in.
    job.save(pipeline=WORKFLOW, collection_id=args.collection, type=args.type, session_id=f"collection-{job.dir.name}",
             started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        name = store.get(store.connect(), args.collection).name
        progress(job, "fetched", f"Collection {name}")
        copy = write_copy(job, args.collection)
        rendered = build(job)
    except (StepError, ValueError) as exc:
        progress(job, "failed", str(exc))
        final_output(f"Collection post stopped: {exc}")
        return 1
    final_output(f"Preview ready: {copy['title']} ({len(rendered)} slides).")
    return 0


def apply_collection_edits(job: Job, feedback: str) -> bool:
    """Item drops and moves, numbered as on the collection's contact sheet; True when the feedback was one."""
    collection_id = int(job.state()["collection_id"])
    connection = store.connect()
    drop = re.fullmatch(r"\s*(?:drop|remove)\s+([\d,\s]+(?:and\s+\d+)?)\s*", feedback, re.I)
    if drop:
        numbers = [int(value) for value in re.findall(r"\d+", drop.group(1))]
        store.remove(connection, collection_id, sorted(set(numbers), reverse=True))
        return True
    move = re.fullmatch(r"\s*move\s+(\d+)\s+to\s+(\d+)\s*", feedback, re.I)
    if move:
        store.move(connection, collection_id, int(move.group(1)), int(move.group(2)))
        return True
    switch = re.fullmatch(r"\s*type\s*:\s*(media|tweets|combo)\s*", feedback, re.I)
    if switch:
        job.save(type=switch.group(1).lower())
        return True
    return False


def command_revise(args: argparse.Namespace) -> int:
    job = job_from_env()
    feedback = args.feedback.strip()
    progress(job, "revision", feedback[:200])
    try:
        if not apply_collection_edits(job, feedback):
            write_copy(job, int(job.state()["collection_id"]), feedback)
        rendered = build(job)
    except (StepError, ValueError) as exc:
        progress(job, "failed", str(exc))
        final_output(f"Revision failed: {exc}")
        return 1
    final_output(f"Revised preview ready: {read_json(job.copy)['title']} ({len(rendered)} slides).")
    return 0


def stage(path: Path) -> str:
    """Copy a slide to the stable HTTPS media host the reel publisher uses and return its public URL."""
    from tools.reels.publish_instagram_reel import load_media_host_config

    host = load_media_host_config()
    host.directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    target = host.directory / f"carousel-{digest}{path.suffix.lower()}"
    if not target.is_file():
        shutil.copyfile(path, target)
    target.chmod(0o644)
    return f"{host.base_url}/{target.name}"


def command_publish(_: argparse.Namespace) -> int:
    job = job_from_env()
    rendered = [Path(path) for path in job.state().get("slides", [])]
    try:
        if len(rendered) < 2 or not all(path.is_file() for path in rendered):
            raise StepError("The approved slides are missing; ask for a new preview.")
        items = [f"{'video' if path.suffix == '.mp4' else 'image'}={stage(path)}" for path in rendered]
    except (StepError, RuntimeError, OSError) as exc:
        progress(job, "failed", f"Could not host the slides: {exc}")
        final_output(f"Instagram publishing stopped before posting: {exc}")
        return 0
    progress(job, "publishing_instagram")
    result = subprocess.run([PY, "tools/news/publish_instagram.py", *[flag for item in items for flag in ("--carousel-item", item)],
                             "--caption-file", str(job.caption), "--skip-website", "--publish", "--confirm", "yes"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    instagram = last_json(result.stdout)
    if result.returncode != 0 or not instagram.get("instagram_media_id"):
        error = (result.stderr.strip() or result.stdout.strip())[-500:]
        progress(job, "failed", f"Instagram publishing failed: {error}")
        final_output(f"Instagram publishing failed (rerun publish to resume): {error}")
        return 0
    link = instagram.get("instagram_permalink") or instagram["instagram_media_id"]
    progress(job, "instagram_done", str(link))
    store.set_status(store.connect(), int(job.state()["collection_id"]), "posted")
    final_output(f"Published to Instagram: {link}\nFacebook and website: skipped (collection)")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--collection", type=int)
    start.add_argument("--type", choices=slides.TYPES)
    start.add_argument("--request", help="The watcher's request text, collection:<id>:<type>.")
    start.set_defaults(handler=command_start)
    revise = commands.add_parser("revise")
    revise.add_argument("--feedback", required=True)
    revise.set_defaults(handler=command_revise)
    commands.add_parser("publish").set_defaults(handler=command_publish)
    args = parser.parse_args(argv)
    if args.command == "start" and args.request:
        args.collection, args.type = parse_request(args.request)
    if args.command == "start" and not (args.collection and args.type):
        parser.error("start needs --collection and --type, or --request")
    try:
        return args.handler(args)
    except Exception as exc:  # a bug or an unexpected error: report it like any failed step
        reason = f"{args.command} stopped unexpectedly: {type(exc).__name__}: {exc}"
        if os.getenv("BITS_TODAY_JOB_DIR"):
            log(job_from_env(), reason)
            progress(job_from_env(), "failed", reason)
        final_output(f"Collection {reason}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
