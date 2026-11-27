#!/usr/bin/env python3
"""Collection posts: one Instagram carousel built from a saved collection.

  start --collection <id> --type media|tweets|combo
                       title and caption -> cover and slides (photos and videos) -> Telegram album preview
  revise --feedback <text>
                       "title: ..." / "caption: ..." set that text exactly and "type: media|tweets|combo" switches
                       the type, with no agent. Anything else starts (or resumes) a Codex session for this post: it
                       sees every slide and the numbered posts, makes the change with `edit`, then runs `rebuild`.
                       A revision that cannot be applied resends the current preview, so the post never fails.
  edit list | move <post> <position> | drop <post>... | type <kind> | title <text> | caption <text>
                       the revision session's tools; post numbers are the collection's order (see `edit list`)
  rebuild              render the slides again and resend the preview
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
    planned, left_out = slides.plan(items, kind)
    if len(planned) < 2:
        raise StepError("The collection needs at least two posts, or one post with more than one photo or video.")
    if job.package.is_dir():
        shutil.rmtree(job.package)
    job.package.mkdir(parents=True)
    seed = f"collection-{collection_id}"
    count = len(planned) + 1
    posted = datetime.now(timezone.utc).date()
    cover_path = job.package / "01.jpg"
    try:
        slides.render_cover(copy["title"], posted, kind, items, planned, job.package, seed).save(cover_path, quality=92)
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


EXACT = re.compile(r"\s*(title|caption)\s*:", re.I)
SWITCH = re.compile(r"\s*type\s*:\s*(media|tweets|combo)\s*", re.I)
REVISION_PROMPT = """The user replied to the Telegram preview of a The Bits Today collection carousel (Instagram) with
feedback. Attached, in order: every slide of the current preview (slide 1 is the cover; a video slide is shown as
a still), then the collection's contact sheet with each post's number.

The posts, in carousel order:
{posts}

USER FEEDBACK START
{feedback}
USER FEEDBACK END

The user may count slides (the cover is slide 1) or posts, and may describe a post by its content ("the deez
notz one"); use the list above to work out which post they mean. Make exactly the changes they asked for, using
only these commands from the repository root:
  python tools/collections/pipeline.py edit list                      the current numbered posts
  python tools/collections/pipeline.py edit move <post> <position>    move a post; "to the last" is position {count}
  python tools/collections/pipeline.py edit drop <post> [<post> ...]  remove posts from the collection
  python tools/collections/pipeline.py edit type media|tweets|combo   Media / Tweets / Media + Tweets
  python tools/collections/pipeline.py edit title "<title>"           the cover title
  python tools/collections/pipeline.py edit caption "<text>"          the caption's paragraph (credits and hashtags
                                                                       are added by the script)
Post numbers change after a move or drop, so run `edit list` again before the next one. When the user describes
a title ("something like ..."), write it from their words: their meaning, Title Case, at most about 70 characters,
no clickbait. Keep a sentence the user quotes exactly as they wrote it.
Then rebuild and resend the preview with exactly one command:
  python tools/collections/pipeline.py rebuild
Do not render, send or publish anything any other way, and never draw or generate images. If a command prints an
error, fix it and run it again. End with one short line saying what changed."""


def post_lines(job: Job) -> list[str]:
    """Each post with its number, author, opening words and where it appears in the current preview."""
    from tools.viral import x_post_card as card

    collection_id, kind = int(job.state()["collection_id"]), job.state()["type"]
    items = store.items(store.connect(), collection_id)
    planned, left_out = slides.plan(items, kind)
    where: dict[int, list[int]] = {}
    for number, slide in enumerate(planned, start=2):
        where.setdefault(slide.item.id, []).append(number)
    lines = []
    for item in items:
        text = " ".join(card.x_text(item.status()).split())
        shown = where.get(item.id)
        place = (f"slide {shown[0]}" if len(shown) == 1 else f"slides {shown[0]}-{shown[-1]}") if shown else "not shown (over the limit)"
        lines.append(f"{item.position}: @{slides.handle(item)} \"{text[:110]}{'...' if len(text) > 110 else ''}\" - {place}")
    return lines


def preview_images(job: Job) -> list[Path]:
    images = []
    for path in (Path(value) for value in job.state().get("slides", [])):
        still = path.with_name(path.stem + "-frame.jpg") if path.suffix == ".mp4" else path
        if still.is_file():
            images.append(still)
    collection_id = int(job.state()["collection_id"])
    sheet = job.dir / "contact-sheet.jpg"
    try:
        slides.contact_sheet(store.items(store.connect(), collection_id), store.get(store.connect(), collection_id).name, sheet)
        images.append(sheet)
    except Exception as exc:  # the sheet only helps the session; never block a revision on it
        log(job, f"contact sheet skipped: {exc}")
    return images


def codex_revision(job: Job, feedback: str) -> str:
    """One turn of this post's revision session (started on the first free-form revision); returns its summary."""
    from tools.news_v2.pipeline import CODEX_MODEL

    turn = job.state().get("codex_turns", 0) + 1
    logs = job.dir / "logs"
    logs.mkdir(exist_ok=True)
    last_message = logs / f"revision-{turn}.final.txt"
    session = job.state().get("codex_session")
    codex = os.getenv("CODEX_BIN") or shutil.which("codex") or "codex"
    command = [codex, "--ask-for-approval", "never", "--model", CODEX_MODEL, "--config", 'model_reasoning_effort="medium"']
    # One comma-joined --image value, then "--": codex's --image takes several values and would otherwise swallow
    # the session ID and the "-" that reads the prompt from stdin.
    images = preview_images(job)
    attach = ["--image=" + ",".join(str(image) for image in images)] if images else []
    if session:
        command += ["--sandbox", "danger-full-access", "exec", "resume", "--json", "--output-last-message",
                    str(last_message), *attach, "--", session, "-"]
    else:
        command += ["exec", "--sandbox", "danger-full-access", "--json", "--cd", str(ROOT), "--output-last-message",
                    str(last_message), *attach, "--", "-"]
    items = store.items(store.connect(), int(job.state()["collection_id"]))
    prompt = REVISION_PROMPT.format(posts="\n".join(post_lines(job)), feedback=feedback, count=len(items))
    log(job, f"revision session turn {turn}{' (resumed)' if session else ''}")
    stdout_path = logs / f"revision-{turn}.jsonl"
    with stdout_path.open("w", encoding="utf-8") as out, (logs / f"revision-{turn}.stderr.log").open("w", encoding="utf-8") as err:
        code = subprocess.run(command, input=prompt, cwd=ROOT, stdout=out, stderr=err, text=True, encoding="utf-8").returncode
    for line in stdout_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started" and event.get("thread_id"):
            session = event["thread_id"]
    job.save(codex_session=session, codex_turns=turn)
    if code != 0:
        raise StepError(f"the revision session exited with status {code}")
    summary = last_message.read_text(encoding="utf-8").strip() if last_message.is_file() else ""
    return summary.splitlines()[-1][:200] if summary else ""


def command_revise(args: argparse.Namespace) -> int:
    job = job_from_env()
    feedback = args.feedback.strip()
    receipt = Path(os.getenv("TELEGRAM_PREVIEW_RECEIPT_PATH", "") or job.dir / "missing-receipt")
    progress(job, "revision", feedback[:200])
    try:
        switch = SWITCH.fullmatch(feedback)
        if switch:
            job.save(type=switch.group(1).lower())
            build(job)
        elif EXACT.match(feedback):
            write_copy(job, int(job.state()["collection_id"]), feedback)
            build(job)
        else:
            summary = codex_revision(job, feedback)
            if summary:
                progress(job, "revision", summary)
            if not receipt.is_file():
                # The session edited the collection but did not rebuild: rebuild for it, so the change reaches Telegram.
                log(job, "no preview after the revision turn; rebuilding")
                build(job)
    except (StepError, ValueError, OSError) as exc:
        # Never lose the post over one revision: say what went wrong and resend the current preview.
        log(job, f"revision not applied: {exc}")
        progress(job, "revision", f"Could not apply that change ({exc}); resending the current preview")
        try:
            if not receipt.is_file():
                build(job)
        except (StepError, ValueError) as again:
            progress(job, "failed", str(again))
            final_output(f"Revision failed: {again}")
            return 1
        final_output(f"Revision not applied: {exc}. The current preview was sent again.")
        return 0
    final_output(f"Revised preview ready: {read_json(job.copy)['title']}.")
    return 0


def command_edit(args: argparse.Namespace) -> int:
    """The revision session's tools. Errors exit 2 with a message the session can act on."""
    job = job_from_env()
    collection_id = int(job.state()["collection_id"])
    connection = store.connect()
    count = store.count(connection, collection_id)
    values = args.values
    try:
        if args.action == "list":
            pass
        elif args.action == "move":
            if len(values) != 2:
                raise ValueError("Use: edit move <post> <position>.")
            store.move(connection, collection_id, int(values[0]), int(values[1]))
        elif args.action == "drop":
            numbers = sorted({int(value) for value in values}, reverse=True)
            if not numbers:
                raise ValueError("Use: edit drop <post> [<post> ...].")
            if any(not 1 <= number <= count for number in numbers):
                raise ValueError(f"Use post numbers from 1 to {count}.")
            if len(numbers) >= count:
                raise ValueError("That would empty the collection; keep at least one post.")
            store.remove(connection, collection_id, numbers)
        elif args.action == "type":
            if len(values) != 1 or values[0] not in slides.TYPES:
                raise ValueError(f"Use: edit type {'|'.join(slides.TYPES)}.")
            job.save(type=values[0])
        elif args.action in ("title", "caption"):
            text = " ".join(" ".join(values).split())
            if not text:
                raise ValueError(f"Use: edit {args.action} \"<text>\".")
            copy = read_json(job.copy)
            copy["title" if args.action == "title" else "explanation"] = text
            job.copy.write_text(json.dumps(copy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if args.action == "title":
                progress(job, "headline", text)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2
    print("\n".join(post_lines(job)))
    print(f"type: {job.state()['type']}  title: {read_json(job.copy)['title']}")
    return 0


def command_rebuild(_: argparse.Namespace) -> int:
    job = job_from_env()
    try:
        rendered = build(job)
    except (StepError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    print(f"Preview sent: {len(rendered)} slides.")
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
    edit = commands.add_parser("edit")
    edit.add_argument("action", choices=("list", "move", "drop", "type", "title", "caption"))
    edit.add_argument("values", nargs="*")
    edit.set_defaults(handler=command_edit)
    commands.add_parser("rebuild").set_defaults(handler=command_rebuild)
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
