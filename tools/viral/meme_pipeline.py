#!/usr/bin/env python3
"""Meme posts: one image with our title on top and the faithful X post below. No agent; every step is a script.

  start --url <x status url>   fetch -> title and caption -> card -> Telegram preview
  revise --feedback <text>     rewrite with the feedback ("title: ..." or "caption: ..." sets that text exactly),
                               re-render and resend the preview
  publish                      after `yes`: Facebook, then Instagram with the same hosted image. Meme posts are
                               not archived on the website.

The job folder is $BITS_TODAY_JOB_DIR; the Telegram watcher runs these commands for the Meme button.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.news_v2.pipeline import StepError, final_output, last_json, log, progress, read_json, run  # noqa: E402

PY = sys.executable
WORKFLOW = "meme"


class Job:
    def __init__(self, folder: Path):
        self.dir = folder
        self.tweet = folder / "tweet.json"
        self.media = folder / "media"
        self.copy = folder / "meme.json"
        self.card = folder / "meme.png"
        self.captions = folder / "captions"
        self.state_path = folder / "meme-state.json"
        self.log = folder / "meme.log"

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


def status_of(job: Job) -> dict:
    return (read_json(job.tweet).get("items") or [{}])[0]


def write_copy(job: Job, feedback: str = "") -> dict:
    command = [PY, "tools/viral/meme_copy.py", "--tweet-json", str(job.tweet), "--output", str(job.copy)]
    if feedback:
        command += ["--feedback", feedback]
    run(job, command, "title and caption")
    meme = read_json(job.copy)
    progress(job, "headline", meme["title"])
    return meme


def build(job: Job) -> None:
    """Render the card, write both captions, and send the preview."""
    from tools.viral import meme_copy, meme_post

    meme = read_json(job.copy)
    progress(job, "generating_items")
    image, _ = meme_post.render(job.tweet, meme["title"], layout="top")
    image.save(job.card)
    job.captions.mkdir(exist_ok=True)
    caption = meme_copy.caption_text(meme, status_of(job))
    for platform in ("facebook", "instagram"):
        (job.captions / f"{platform}-description.txt").write_text(caption, encoding="utf-8")
    progress(job, "items_ready", "1 card")
    run(job, [PY, "tools/news/notify_telegram.py", "--image", str(job.card), "--description-file",
              str(job.captions / "facebook-description.txt"), "--stage", "preview", "--send", "--platform", "both"],
        "preview")
    progress(job, "preview", "1 card")
    job.save(title=meme["title"])


def command_start(args: argparse.Namespace) -> int:
    job = job_from_env()
    # The watcher keeps a session ID per job; memes have no agent session, so the job's own name stands in.
    job.save(pipeline=WORKFLOW, url=args.url, session_id=f"meme-{job.dir.name}",
             started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        progress(job, "fetching")
        job.media.mkdir(exist_ok=True)
        run(job, [PY, "tools/news/fetch_tweets.py", args.url, "--facebook-language", "english", "--instagram-language",
                  "english", "--output", str(job.tweet), "--media-dir", str(job.media)], "fetch")
        tweet = read_json(job.tweet)
        item = (tweet.get("items") or [{}])[0]
        if not str(item.get("text", "")).strip() and not (item.get("media") or {}).get("all"):
            raise StepError("The fetched post has no text or media.")
        tweet["workflow_type"] = WORKFLOW
        job.tweet.write_text(json.dumps(tweet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        progress(job, "fetched", f"{len(item.get('downloaded_photos') or [])} photos")
        meme = write_copy(job)
        build(job)
    except StepError as exc:
        progress(job, "failed", str(exc))
        final_output(f"Meme stopped: {exc}")
        return 1
    final_output(f"Preview ready: {meme['title']}.")
    return 0


def command_revise(args: argparse.Namespace) -> int:
    job = job_from_env()
    progress(job, "revision", args.feedback[:200])
    try:
        meme = write_copy(job, args.feedback.strip())
        build(job)
    except StepError as exc:
        progress(job, "failed", str(exc))
        final_output(f"Revision failed: {exc}")
        return 1
    final_output(f"Revised preview ready: {meme['title']}.")
    return 0


def command_publish(_: argparse.Namespace) -> int:
    job = job_from_env()
    facebook_caption = job.captions / "facebook-description.txt"
    instagram_caption = job.captions / "instagram-description.txt"
    progress(job, "publishing_facebook")
    result = subprocess.run([PY, "tools/news/publish_facebook.py", "--image", str(job.card), "--message-file",
                             str(facebook_caption), "--skip-website", "--publish", "--confirm", "yes"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    facebook = last_json(result.stdout)
    if result.returncode != 0 or not facebook.get("facebook_image_urls"):
        error = (result.stderr.strip() or result.stdout.strip())[-500:]
        progress(job, "failed", f"Facebook publishing failed: {error}")
        final_output(f"Facebook publishing failed; nothing was posted to Instagram.\n{error}")
        return 0
    facebook_link = facebook.get("facebook_permalink") or facebook.get("facebook_post_id")
    progress(job, "facebook_done", str(facebook_link))
    progress(job, "publishing_instagram")
    result = subprocess.run([PY, "tools/news/publish_instagram.py", "--image-url", facebook["facebook_image_urls"][0],
                             "--caption-file", str(instagram_caption), "--skip-website", "--publish", "--confirm", "yes"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    instagram = last_json(result.stdout)
    if result.returncode != 0 or not (instagram.get("media_id") or instagram.get("instagram_media_id")):
        error = (result.stderr.strip() or result.stdout.strip())[-500:]
        progress(job, "failed", f"Instagram failed after Facebook published ({facebook_link}): {error}")
        final_output(f"Facebook published: {facebook_link}\nInstagram failed (rerun publish to resume): {error}")
        return 0
    instagram_link = instagram.get("instagram_permalink") or instagram.get("instagram_media_id")
    progress(job, "instagram_done", str(instagram_link))
    final_output(f"Published.\nFacebook: {facebook_link}\nInstagram: {instagram_link}\nWebsite archive: skipped (meme)")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--url", required=True)
    start.set_defaults(handler=command_start)
    revise = commands.add_parser("revise")
    revise.add_argument("--feedback", required=True)
    revise.set_defaults(handler=command_revise)
    commands.add_parser("publish").set_defaults(handler=command_publish)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except Exception as exc:  # a bug or an unexpected error: report it like any failed step
        reason = f"{args.command} stopped unexpectedly: {type(exc).__name__}: {exc}"
        if os.getenv("BITS_TODAY_JOB_DIR"):
            log(job_from_env(), reason)
            progress(job_from_env(), "failed", reason)
        final_output(f"Meme {reason}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
