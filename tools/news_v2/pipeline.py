#!/usr/bin/env python3
"""News v2: research by one Codex session, everything else by a fixed chain of scripts.

  start --url <x status url>   fetch -> research session -> headline -> description -> sources -> media ->
                               cover -> slide text -> carousel -> captions -> Telegram preview
  revise --feedback <text>     resume the post's research session with the feedback, every current slide and the
                               media contact sheet attached; the agent edits the job's inputs and runs `rebuild`
  rebuild [--new-copy] [--new-plan] [--recollect] [--new-description]
                               re-render from the job's inputs and resend the preview (the agent's only command)
  publish                      after `yes`: Facebook, then Instagram with the same hosted images; no agent
  use v1|v2                    choose which news pipeline new watcher jobs use

The job folder is $BITS_TODAY_JOB_DIR. The Telegram watcher runs these commands; tools/dev/agent.py runs them
locally with previews saved to files.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.news import generate_description as news_description  # noqa: E402
from tools.news import media_plan  # noqa: E402
from tools.news_v2 import brief as research_brief  # noqa: E402

PY = sys.executable
SELECTION = ROOT / ".automation" / "news-pipeline"
CODEX_MODEL = os.getenv("BITS_TODAY_CODEX_MODEL", "gpt-6-luna")
DESCRIPTION_TOKENS = 1500  # the description script's own default

RESEARCH_PROMPT = """Read tools/news_v2/RESEARCH.md and follow it exactly.

The X post for this job is {url}. It has been fetched to $BITS_TODAY_JOB_DIR/tweet.json, with its photos in
$BITS_TODAY_JOB_DIR/media/. This turn is research only: end once research-brief.json validates."""

REVISION_PROMPT = """The user replied to the Telegram preview of this post with revision feedback. Attached, in
order: every slide of the current preview (the first is the headline card), then the contact sheet of all collected
media (t# are the X post's photos, s# are images from primary sources; logos and duplicates are labelled).

USER FEEDBACK START
{feedback}
USER FEEDBACK END

Make exactly the changes the user asked for, using your research. You may edit only these files in
$BITS_TODAY_JOB_DIR:
- headline.json: its "headline" field.
- copy.json: "short_descriptions", the text of each story slide in order, at most 160 characters each.
- media-plan.json: the cover and slides, by the IDs on the contact sheet, following step 7 of
  tools/news/WORKFLOW.md. To get new images, add official pages to "media_pages" in research-brief.json and use
  --recollect.
- finalized.txt: the caption text above its "Sources:" block.
- research-brief.json: facts and sources, when the feedback needs more research.

Then rebuild and resend the preview with exactly one command:
  python tools/news_v2/pipeline.py rebuild [--new-copy] [--new-plan] [--recollect] [--new-description]
  --new-copy          write fresh slide text (when the slides should change and you did not write them yourself)
  --new-plan          let the picker choose the media again
  --recollect         collect media again after adding media_pages
  --new-description   rewrite the caption from the brief
Do not render, send or publish anything any other way, and never draw or generate images. If rebuild prints an
error, fix the file it names and run it again. End with one line saying what changed."""


class StepError(RuntimeError):
    pass


class Job:
    def __init__(self, folder: Path):
        self.dir = folder
        self.tweet = folder / "tweet.json"
        self.media = folder / "media"
        self.brief = folder / "research-brief.json"
        self.headline = folder / "headline.json"
        self.description = folder / "description.txt"
        self.finalized = folder / "finalized.txt"
        self.sources = folder / "finalized.sources.json"
        self.source_media = folder / "source-media"
        self.plan = folder / "media-plan.json"
        self.resolved = folder / "media-plan.resolved.json"
        self.primary = folder / "primary.png"
        self.copy = folder / "copy.json"
        self.package = folder / "package"
        self.captions = folder / "captions"
        self.state_path = folder / "v2-state.json"
        self.log = folder / "v2.log"

    def state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.is_file() else {}

    def save(self, **values) -> None:
        state = {**self.state(), **values}
        self.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def job_from_env() -> Job:
    folder = os.getenv("BITS_TODAY_JOB_DIR", "").strip()
    if not folder:
        raise SystemExit("BITS_TODAY_JOB_DIR is not set.")
    path = Path(folder)
    path.mkdir(parents=True, exist_ok=True)
    return Job(path)


def log(job: Job, text: str) -> None:
    with job.log.open("a", encoding="utf-8") as handle:
        handle.write(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {text}\n")
    print(text, flush=True)


def run(job: Job, command: list[str], what: str) -> str:
    log(job, f"$ {what}")
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip())[-800:]
        log(job, f"failed: {detail}")
        raise StepError(f"{what} failed: {detail}")
    return result.stdout


def progress(job: Job, stage: str, detail: str | None = None) -> None:
    command = [PY, "tools/news/report_progress.py", "--stage", stage]
    if detail:
        command += ["--detail", detail[:480]]
    subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def final_output(text: str) -> None:
    path = os.getenv("BITS_TODAY_FINAL_OUTPUT", "").strip()
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text + "\n", encoding="utf-8")
    print(text)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def tweet_url(job: Job) -> str:
    tweet = read_json(job.tweet)
    return (tweet.get("requested_urls") or [tweet["items"][0].get("url")])[0]


# ------------------------------------------------------------------------------------------------- research
def codex_command(*, resume: bool, session: str | None, images: list[Path], last_message: Path) -> list[str]:
    codex = os.getenv("CODEX_BIN") or shutil.which("codex") or "codex"
    command = [codex, "--search", "--ask-for-approval", "never"]
    if os.getenv("BITS_TODAY_CODEX_PROVIDER") == "openrouter":
        command += ["--model", os.getenv("BITS_TODAY_DEV_MODEL", "openai/gpt-6-luna"),
                    "--config", 'model_provider="openrouter"',
                    "--config", 'model_providers.openrouter.name="OpenRouter"',
                    "--config", 'model_providers.openrouter.base_url="https://openrouter.ai/api/v1"',
                    "--config", 'model_providers.openrouter.env_key="OPENROUTER_API_KEY"',
                    "--config", 'model_providers.openrouter.wire_api="responses"']
    else:
        command += ["--model", CODEX_MODEL]
    command += ["--config", 'model_reasoning_effort="medium"']
    if resume:
        attach = [flag for image in images for flag in ("--image", str(image))]
        return [*command, "--sandbox", "danger-full-access", "exec", "resume", "--json", "--output-last-message",
                str(last_message), *attach, str(session), "-"]
    return [*command, "exec", "--sandbox", "danger-full-access", "--json", "--cd", str(ROOT),
            "--output-last-message", str(last_message), "-"]


def codex_turn(job: Job, prompt: str, *, resume: bool, images: list[Path] | None = None) -> str | None:
    """Run one research-session turn; returns the session ID."""
    turn = job.state().get("codex_turns", 0) + 1
    logs = job.dir / "logs"
    logs.mkdir(exist_ok=True)
    stdout_path = logs / f"research-{turn}.jsonl"
    command = codex_command(resume=resume, session=job.state().get("session_id"), images=images or [],
                            last_message=logs / f"research-{turn}.final.txt")
    log(job, f"research session turn {turn}{' (resumed)' if resume else ''}")
    with stdout_path.open("w", encoding="utf-8") as out, (logs / f"research-{turn}.stderr.log").open("w", encoding="utf-8") as err:
        code = subprocess.run(command, input=prompt, cwd=ROOT, stdout=out, stderr=err, text=True,
                              encoding="utf-8").returncode
    session = job.state().get("session_id")
    for line in stdout_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started" and event.get("thread_id"):
            session = event["thread_id"]
    job.save(session_id=session, codex_turns=turn)
    if code != 0:
        raise StepError(f"The research session exited with status {code}.")
    return session


def research(job: Job, url: str) -> dict:
    progress(job, "research_started")
    codex_turn(job, RESEARCH_PROMPT.format(url=url), resume=False)
    for attempt in range(2):
        issues = []
        if not job.brief.is_file():
            issues = ["research-brief.json was not written."]
        else:
            try:
                research_brief.load(job.brief)
            except (ValueError, OSError) as exc:
                issues = [str(exc)]
        if not issues:
            break
        if attempt == 1:
            raise StepError("The research brief is still invalid: " + " ".join(issues))
        codex_turn(job, "research-brief.json is not valid yet: " + " ".join(issues) +
                   " Fix it and run tools/news_v2/brief.py until it prints valid.", resume=True)
    brief = research_brief.load(job.brief)
    progress(job, "research_complete", f"{len(brief.get('source_urls', []))} useful sources")
    return brief


# ---------------------------------------------------------------------------------------------- script chain
def write_headline(job: Job) -> str:
    run(job, [PY, "tools/news_v2/headline.py", "--tweet-json", str(job.tweet), "--brief", str(job.brief),
              "--output", str(job.headline)], "headline")
    headline = read_json(job.headline)["headline"]
    progress(job, "headline", headline)
    return headline


def write_description(job: Job, brief: dict) -> None:
    facts = "\n".join(f"- {item['fact']}" for item in brief.get("facts", []))
    source_text = news_description.normalize_source_text(
        f"{news_description.read_tweet_text(job.tweet)}\n\nVerified context from research:\n{brief.get('summary', '')}\n{facts}")
    log(job, "$ description")
    news_description.require_api_key()
    try:
        text = news_description.generate_description(news_description.make_client(), source_text,
                                                     max_output_tokens=DESCRIPTION_TOKENS)
    except Exception as exc:
        raise StepError(f"description failed: {type(exc).__name__}: {exc}")
    if not text:
        raise StepError("The description came back empty.")
    job.description.write_text(text + "\n", encoding="utf-8")
    sources = []
    for url in [tweet_url(job), *brief.get("source_urls", [])]:
        sources += ["--source-url", url]
    run(job, [PY, "tools/news/finalize_description.py", "--description-file", str(job.description), "--tweet-json",
              str(job.tweet), *sources, "--output", str(job.finalized)], "sources")
    progress(job, "description")


def collect(job: Job, brief: dict) -> None:
    pages = [flag for url in brief.get("media_pages", []) for flag in ("--media-page", url)]
    run(job, [PY, "tools/news/collect_source_media.py", "--tweet-json", str(job.tweet), "--sources", str(job.sources),
              *pages, "--output-dir", str(job.source_media)], "collect source media")
    manifest = read_json(job.source_media / "source-media.json")
    progress(job, "media_ready", f"{len(manifest['tweet_photos'])} post photos, {len(manifest['images'])} source images")


def pick(job: Job, headline: str) -> None:
    output = run(job, [PY, "tools/news_v2/pick_media.py", "--source-media", str(job.source_media / "source-media.json"),
                       "--headline", headline, "--brief", str(job.brief), "--output", str(job.plan)], "media plan")
    log(job, output.strip()[-400:])


def resolve_plan(job: Job) -> dict:
    """Re-check the plan (the agent may have edited it) and write the resolved copy the renderers read."""
    try:
        resolved = media_plan.resolve(read_json(job.plan), media_plan.load_manifest(job.source_media / "source-media.json"))
    except (ValueError, KeyError, OSError) as exc:
        raise StepError(f"media-plan.json is invalid: {exc}")
    job.resolved.write_text(json.dumps(resolved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resolved


def build(job: Job, *, new_copy: bool) -> list[Path]:
    """Cover, slide text, carousel, captions and the Telegram preview, from the job's current inputs."""
    progress(job, "generating_items")
    brief = research_brief.load(job.brief)
    headline = read_json(job.headline)["headline"].strip()
    if not headline:
        raise StepError("headline.json has an empty headline.")
    resolved = resolve_plan(job)
    cover = [PY, "tools/news/generate_post.py", brief["summary"], "--headline", headline, "--tweet-json", str(job.tweet),
             "--style", "cinema", "--media-plan", str(job.resolved), "--output", str(job.primary)]
    background = job.primary.with_name("primary-background.png")
    if background.is_file():
        cover += ["--background-input", str(background)]
    run(job, cover, "cover")

    text_slides = sum(1 for slide in resolved["slides"] if slide["kind"] == "detail")
    existing = read_json(job.copy) if job.copy.is_file() else {}
    if new_copy or len(existing.get("short_descriptions", [])) != text_slides or existing.get("mode") != "plan":
        run(job, [PY, "tools/news/generate_carousel_copy.py", "--tweet-json", str(job.tweet), "--post-metadata",
                  str(job.primary.with_suffix(".json")), "--description-file", str(job.finalized), "--output",
                  str(job.copy), "--media-plan", str(job.resolved)], "slide text")
    else:
        existing["media_plan"] = str(job.resolved.resolve())
        job.copy.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if job.package.is_dir():
        shutil.rmtree(job.package)
    run(job, [PY, "tools/news/generate_carousel.py", "--tweet-json", str(job.tweet), "--primary-image", str(job.primary),
              "--copy-json", str(job.copy), "--output-dir", str(job.package), "--platform", "facebook"], "carousel")
    run(job, [PY, "tools/news/prepare_platform_descriptions.py", "--description-file", str(job.finalized), "--tweet-json",
              str(job.tweet), "--output-dir", str(job.captions), "--english-title", headline], "captions")
    carousel = read_json(job.package / "carousel.json")
    slides = [Path(carousel["primary_image"]), *map(Path, carousel["secondary_images"])]
    progress(job, "items_ready", f"{len(slides)} cards")

    facebook = job.captions / "facebook-description.txt"
    instagram = job.captions / "instagram-description.txt"
    images = ["--image", str(slides[0]), *[flag for path in slides[1:] for flag in ("--secondary-image", str(path))]]
    same = facebook.read_text(encoding="utf-8") == instagram.read_text(encoding="utf-8")
    for platform, caption in ([("both", facebook)] if same else [("facebook", facebook), ("instagram", instagram)]):
        run(job, [PY, "tools/news/notify_telegram.py", *images, "--description-file", str(caption), "--stage", "preview",
                  "--send", "--platform", platform], f"preview ({platform})")
    progress(job, "preview", f"{len(slides)} cards")
    job.save(headline=headline, slides=[str(path) for path in slides])
    return slides


# ------------------------------------------------------------------------------------------------- commands
def command_start(args: argparse.Namespace) -> int:
    job = job_from_env()
    job.save(pipeline="v2", url=args.url, started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        progress(job, "fetching")
        job.media.mkdir(exist_ok=True)
        run(job, [PY, "tools/news/fetch_tweets.py", args.url, "--facebook-language", "english", "--instagram-language",
                  "english", "--output", str(job.tweet), "--media-dir", str(job.media)], "fetch")
        tweet = read_json(job.tweet)
        item = (tweet.get("items") or [{}])[0]
        if not str(item.get("text", "")).strip():
            raise StepError("The fetched post has no text.")
        tweet["workflow_type"] = "news"
        job.tweet.write_text(json.dumps(tweet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        progress(job, "fetched", f"{len(item.get('downloaded_photos') or [])} photos")
        brief = research(job, args.url) if not args.brief else json.loads(Path(args.brief).read_text(encoding="utf-8-sig"))
        if args.brief:
            shutil.copy2(args.brief, job.brief)
        headline = write_headline(job)
        write_description(job, brief)
        collect(job, brief)
        pick(job, headline)
        slides = build(job, new_copy=True)
    except StepError as exc:
        progress(job, "failed", str(exc))
        final_output(f"News v2 stopped: {exc}")
        return 1
    final_output(f"Preview ready: {headline} ({len(slides)} cards).")
    return 0


def command_rebuild(args: argparse.Namespace) -> int:
    job = job_from_env()
    try:
        brief = research_brief.load(job.brief)
        if args.new_description:
            write_description(job, brief)
        if args.recollect:
            collect(job, brief)
        if args.recollect or args.new_plan:
            pick(job, read_json(job.headline)["headline"])
        slides = build(job, new_copy=args.new_copy)
    except (StepError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Preview resent: {len(slides)} cards.")
    return 0


def command_revise(args: argparse.Namespace) -> int:
    job = job_from_env()
    if not job.state().get("session_id"):
        final_output("News v2 stopped: this post has no research session to resume.")
        return 1
    receipt = Path(os.getenv("TELEGRAM_PREVIEW_RECEIPT_PATH", "") or job.dir / "missing-receipt")
    images = [Path(path) for path in job.state().get("slides", []) if Path(path).is_file()]
    sheet = job.source_media / "contact-sheet.jpg"
    if sheet.is_file():
        images.append(sheet)
    progress(job, "revision", args.feedback[:200])
    try:
        codex_turn(job, REVISION_PROMPT.format(feedback=args.feedback.strip()), resume=True, images=images)
        if not receipt.is_file():
            # The agent edited inputs but did not rebuild: rebuild for it, so the revision still reaches Telegram.
            log(job, "no preview after the revision turn; rebuilding")
            build(job, new_copy=False)
    except StepError as exc:
        progress(job, "failed", str(exc))
        final_output(f"Revision failed: {exc}")
        return 1
    final_output(f"Revised preview ready: {read_json(job.headline)['headline']}.")
    return 0


def last_json(text: str) -> dict:
    start = text.rfind("\n{")
    for candidate in (text, text[start + 1:] if start >= 0 else text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return {}


def command_publish(_: argparse.Namespace) -> int:
    job = job_from_env()
    carousel = read_json(job.package / "carousel.json")
    slides = [carousel["primary_image"], *carousel["secondary_images"]]
    facebook_caption = job.captions / "facebook-description.txt"
    instagram_caption = job.captions / "instagram-description.txt"
    progress(job, "publishing_facebook")
    command = [PY, "tools/news/publish_facebook.py", "--image", slides[0],
               *[flag for path in slides[1:] for flag in ("--secondary-image", path)],
               "--message-file", str(facebook_caption), "--publish", "--confirm", "yes"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    facebook = last_json(result.stdout)
    if result.returncode != 0 or not facebook.get("facebook_image_urls"):
        error = (result.stderr.strip() or result.stdout.strip())[-500:]
        progress(job, "failed", f"Facebook publishing failed: {error}")
        final_output(f"Facebook publishing failed; nothing was posted to Instagram.\n{error}")
        return 0
    facebook_link = facebook.get("facebook_permalink") or facebook.get("facebook_post_id")
    progress(job, "facebook_done", str(facebook_link))
    urls = facebook["facebook_image_urls"]
    progress(job, "publishing_instagram")
    command = [PY, "tools/news/publish_instagram.py", "--image-url", urls[0],
               *[flag for url in urls[1:] for flag in ("--secondary-image-url", url)],
               "--caption-file", str(instagram_caption), "--publish", "--confirm", "yes"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    instagram = last_json(result.stdout)
    archive = facebook.get("website_archive")
    if result.returncode != 0 or not (instagram.get("media_id") or instagram.get("instagram_media_id")):
        error = (result.stderr.strip() or result.stdout.strip())[-500:]
        progress(job, "failed", f"Instagram failed after Facebook published ({facebook_link}): {error}")
        final_output(f"Facebook published: {facebook_link}\nInstagram failed (rerun publish to resume): {error}")
        return 0
    instagram_link = instagram.get("instagram_permalink") or instagram.get("instagram_media_id")
    progress(job, "instagram_done", str(instagram_link))
    final_output(f"Published.\nFacebook: {facebook_link}\nInstagram: {instagram_link}\n"
                 f"Website archive: {json.dumps(archive) if archive else 'n/a'}")
    return 0


def command_use(args: argparse.Namespace) -> int:
    SELECTION.parent.mkdir(parents=True, exist_ok=True)
    SELECTION.write_text(args.version + "\n", encoding="utf-8")
    print(f"New news jobs now use {args.version}. Jobs already started keep their pipeline.")
    return 0


def selected() -> str:
    """The pipeline new news jobs use: v1 unless switched with `pipeline.py use v2`."""
    try:
        value = SELECTION.read_text(encoding="utf-8").strip()
    except OSError:
        return "v1"
    return value if value in ("v1", "v2") else "v1"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--url", required=True)
    start.add_argument("--brief", help="Skip the research session and use this brief (testing only).")
    start.set_defaults(handler=command_start)
    revise = commands.add_parser("revise")
    revise.add_argument("--feedback", required=True)
    revise.set_defaults(handler=command_revise)
    rebuild = commands.add_parser("rebuild")
    for flag in ("--new-copy", "--new-plan", "--recollect", "--new-description"):
        rebuild.add_argument(flag, action="store_true")
    rebuild.set_defaults(handler=command_rebuild)
    commands.add_parser("publish").set_defaults(handler=command_publish)
    use = commands.add_parser("use")
    use.add_argument("version", choices=("v1", "v2"))
    use.set_defaults(handler=command_use)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except Exception as exc:  # a bug or an unexpected error: report it like any failed step
        reason = f"{args.command} stopped unexpectedly: {type(exc).__name__}: {exc}"
        if os.getenv("BITS_TODAY_JOB_DIR"):
            log(job_from_env(), reason)
            progress(job_from_env(), "failed", reason)
        final_output(f"News v2 {reason}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
