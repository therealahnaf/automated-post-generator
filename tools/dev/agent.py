#!/usr/bin/env python3
"""Local dev channel for The Bits Today agent: run the real workflow from the CLI instead of Telegram.

It drives the same Codex CLI call the Telegram watcher makes (same prompts, flags, environment and job layout), but
routes the model through OpenRouter (`openai/gpt-6-luna` by default) and sets BITS_TODAY_DEV_DIR, so previews and
progress land in local files and every publisher refuses to run. Nothing is sent to Telegram or published.

  python tools/dev/agent.py new <x-status-url> [--workflow news|model|product|informative|reel|auto]
  python tools/dev/agent.py revise <job> "feedback, exactly as you would type it in Telegram"
  python tools/dev/agent.py show <job>        # latest preview: slides, caption, contact sheet
  python tools/dev/agent.py list

State lives in .automation/dev/ (git-ignored): one folder per job with the agent's artifacts, its Codex logs,
preview packages, the progress log and a contact sheet of every preview.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.news.telegram_codex_watcher import (  # noqa: E402
    CODEX_MODEL_REASONING_EFFORT,
    WORKFLOW_TYPES,
    X_STATUS_URL_RE,
    build_initial_prompt,
    build_revision_prompt,
    parse_session_id,
)

DEV_ROOT = ROOT / ".automation" / "dev"
STATE = DEV_ROOT / "state.json"
DEFAULT_MODEL = "openai/gpt-6-luna"
# Dev job IDs stay far from the watcher's, so a stray report can never be mistaken for a real job.
JOB_ID_BASE = 900000
# Told to the agent up front, so it treats the local preview as the delivery it would make in Telegram.
DEV_NOTE = (
    "\n\nDEV RUN: this session is a local test of the workflow, not the Telegram watcher. "
    "`tools/news/notify_telegram.py --send` saves the preview package locally for the developer to review; a "
    "successful run of it counts as delivering the Telegram preview, so continue exactly as you would after a "
    "real send. `report_progress.py` writes to a local log. Publishing commands are disabled in this session."
)


def read_env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_state() -> dict:
    if STATE.is_file():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"jobs": {}}


def save_state(state: dict) -> None:
    DEV_ROOT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def codex_binary() -> str:
    found = os.getenv("CODEX_BIN") or shutil.which("codex")
    if not found:
        raise SystemExit("Codex CLI not found; install it or set CODEX_BIN.")
    return found


def codex_prefix(model: str) -> list[str]:
    """The watcher's command prefix, with the model provider switched to OpenRouter."""
    return [
        codex_binary(),
        "--search",
        "--ask-for-approval", "never",
        "--model", model,
        "--config", f'model_reasoning_effort="{CODEX_MODEL_REASONING_EFFORT}"',
        "--config", 'model_provider="openrouter"',
        "--config", 'model_providers.openrouter.name="OpenRouter"',
        "--config", 'model_providers.openrouter.base_url="https://openrouter.ai/api/v1"',
        "--config", 'model_providers.openrouter.env_key="OPENROUTER_API_KEY"',
        "--config", 'model_providers.openrouter.wire_api="responses"',
    ]


def environment(job: dict, receipt: Path) -> dict[str, str]:
    env = os.environ.copy()
    secrets = read_env_file()
    if not secrets.get("OPENROUTER_API_KEY") and not env.get("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is missing from .env.")
    env.setdefault("OPENROUTER_API_KEY", secrets.get("OPENROUTER_API_KEY", ""))
    job_dir = Path(job["dir"])
    venv_bin = ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    env.update({
        "PATH": f"{venv_bin}{os.pathsep}{env.get('PATH', '')}",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        # Inside the job's artifact folder, which the workflow tells the agent to keep everything in.
        "BITS_TODAY_DEV_DIR": str(job_dir / "artifacts" / "dev-preview"),
        "TELEGRAM_REPLY_TO_MESSAGE_ID": "1",
        "TELEGRAM_PREVIEW_RECEIPT_PATH": str(receipt),
        "TELEGRAM_WATCHER_JOB_ID": str(job["id"]),
        "BITS_TODAY_WORKFLOW_TYPE": job["workflow"],
        "BITS_TODAY_POST_LANGUAGE": "english",
        "BITS_TODAY_FACEBOOK_LANGUAGE": "english",
        "BITS_TODAY_INSTAGRAM_LANGUAGE": "english",
        "BITS_TODAY_JOB_DIR": str(job_dir / "artifacts"),
    })
    return env


def describe(event: dict) -> str | None:
    """One readable console line per interesting Codex event."""
    item = event.get("item") or {}
    kind = item.get("type")
    if event.get("type") == "item.completed" and kind == "agent_message":
        return f"\n[agent] {item.get('text', '').strip()}\n"
    if event.get("type") == "item.started" and kind == "command_execution":
        command = " ".join(str(item.get("command", "")).split())
        return f"[run] {command[:220]}"
    if event.get("type") == "item.completed" and kind == "command_execution":
        code = item.get("exit_code")
        return None if code == 0 else f"[exit {code}] {str(item.get('aggregated_output', ''))[-400:].strip()}"
    if event.get("type") == "item.completed" and kind == "web_search":
        return f"[search] {item.get('query', '')}"
    if event.get("type") == "item.completed" and kind == "file_change":
        return "[edit] " + ", ".join(change.get("path", "") for change in item.get("changes", []))[:220]
    if event.get("type") == "turn.completed":
        usage = event.get("usage") or {}
        return f"[turn done] input {usage.get('input_tokens')} tokens, output {usage.get('output_tokens')}"
    if event.get("type") in ("turn.failed", "error"):
        return f"[error] {json.dumps(event)[:400]}"
    return None


def run_turn(job: dict, prompt: str, resume: bool, model: str) -> int:
    job_dir = Path(job["dir"])
    turn = job["turns"] + 1
    logs = job_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (job_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    stdout_path = logs / f"turn-{turn}.jsonl"
    final_path = logs / f"turn-{turn}.final.txt"
    receipt = job_dir / "receipts" / f"turn-{turn}.json"
    if resume:
        command = [*codex_prefix(model), "--sandbox", "danger-full-access", "exec", "resume", "--json",
                   "--output-last-message", str(final_path), job["session_id"], "-"]
    else:
        command = [*codex_prefix(model), "exec", "--sandbox", "danger-full-access", "--json", "--cd", str(ROOT),
                   "--output-last-message", str(final_path), "-"]
    (logs / f"turn-{turn}.prompt.txt").write_text(prompt, encoding="utf-8")
    started = time.time()
    print(f"== dev job {job['name']} turn {turn} ({model}) ==")
    with stdout_path.open("w", encoding="utf-8") as log, (logs / f"turn-{turn}.stderr.log").open("w", encoding="utf-8") as errors:
        process = subprocess.Popen(command, cwd=ROOT, env=environment(job, receipt), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8")
        assert process.stdin and process.stdout
        process.stdin.write(prompt)
        process.stdin.close()
        for line in process.stdout:
            log.write(line)
            log.flush()
            try:
                message = describe(json.loads(line))
            except json.JSONDecodeError:
                continue
            if message:
                print(message, flush=True)
        code = process.wait()
    session = parse_session_id(stdout_path) or job.get("session_id")
    state = load_state()
    stored = state["jobs"][job["name"]]
    stored.update(session_id=session, turns=turn, last_exit=code,
                  last_turn_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    save_state(state)
    minutes = (time.time() - started) / 60
    print(f"\n== turn {turn} finished in {minutes:.1f} min, exit {code}; preview receipt "
          f"{'written' if receipt.is_file() else 'MISSING (no preview delivered)'} ==")
    if receipt.is_file():
        show(stored)
    return code


def contact_sheet(images: list[Path], target: Path) -> None:
    from PIL import Image

    thumb = (324, 405)
    sheet = Image.new("RGB", (len(images) * (thumb[0] + 10) + 10, thumb[1] + 20), (30, 30, 32))
    for index, path in enumerate(images):
        try:
            with Image.open(path) as image:
                sheet.paste(image.convert("RGB").resize(thumb), (10 + index * (thumb[0] + 10), 10))
        except OSError:
            continue
    sheet.save(target, quality=88)


def show(job: dict) -> None:
    roots = [Path(job["dir"]) / "artifacts" / "dev-preview", Path(job["dir"]) / "dev"]
    folders = sorted((path for root in roots for path in (root / "previews").glob("*") if path.is_dir()),
                     key=lambda path: path.stat().st_mtime)
    if not folders:
        print("No preview delivered yet.")
        return
    latest = folders[-1]
    slides = sorted(path for path in latest.iterdir()
                    if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".mp4") and path.name != "contact-sheet.jpg")
    images = [path for path in slides if path.suffix.lower() != ".mp4"]
    if images:
        contact_sheet(images, latest / "contact-sheet.jpg")
    print(f"Latest preview: {latest}")
    for path in slides:
        print("  slide:", path)
    if images:
        print("  contact sheet:", latest / "contact-sheet.jpg")
    caption = latest / "caption.txt"
    if caption.is_file():
        print("\n--- caption ---\n" + caption.read_text(encoding="utf-8-sig").strip() + "\n---------------")
    progress = next((root / "progress.jsonl" for root in roots if (root / "progress.jsonl").is_file()), None)
    if progress is not None:
        stages = [json.loads(line) for line in progress.read_text(encoding="utf-8").splitlines() if line.strip()]
        print("progress:", " > ".join(f"{item['stage']}" + (f" ({item['detail']})" if item.get("detail") else "")
                                      for item in stages)[:900])


def run_v2(job: dict, arguments: list[str], model: str) -> int:
    """News v2 locally: the same pipeline.py the watcher runs, with the research session on OpenRouter."""
    turn = job["turns"] + 1
    receipt = Path(job["dir"]) / "receipts" / f"turn-{turn}.json"
    env = environment(job, receipt)
    env.update({"BITS_TODAY_CODEX_PROVIDER": "openrouter", "BITS_TODAY_DEV_MODEL": model,
                "BITS_TODAY_FINAL_OUTPUT": str(Path(job["dir"]) / "logs" / f"turn-{turn}.final.txt")})
    (Path(job["dir"]) / "logs").mkdir(parents=True, exist_ok=True)
    print(f"== dev job {job['name']} turn {turn} (news v2: {arguments[0]}) ==", flush=True)
    code = subprocess.run([sys.executable, str(ROOT / "tools" / "news_v2" / "pipeline.py"), *arguments],
                          cwd=ROOT, env=env).returncode
    state = load_state()
    state["jobs"][job["name"]].update(turns=turn, last_exit=code)
    save_state(state)
    show(state["jobs"][job["name"]])
    return code


def command_new(args: argparse.Namespace) -> int:
    if not X_STATUS_URL_RE.fullmatch(args.url.strip()):
        raise SystemExit("Give an x.com or twitter.com status URL.")
    state = load_state()
    number = 1 + len(state["jobs"])
    name = f"dev-{number}"
    job = {"name": name, "id": JOB_ID_BASE + number, "url": args.url.strip(), "workflow": args.workflow,
           "dir": str(DEV_ROOT / "jobs" / name), "turns": 0, "session_id": None,
           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    state["jobs"][name] = job
    save_state(state)
    if args.pipeline == "v2":
        if args.workflow != "news":
            raise SystemExit("News v2 runs only the news workflow; pass --workflow news.")
        job["pipeline"] = "v2"
        state["jobs"][name] = job
        save_state(state)
        return run_v2(job, ["start", "--url", job["url"]], args.model)
    prompt = build_initial_prompt(job["id"], job["url"], workflow_type=args.workflow) + DEV_NOTE
    return run_turn(job, prompt, resume=False, model=args.model)


def find_job(name: str) -> dict:
    state = load_state()
    key = name if name in state["jobs"] else f"dev-{name}"
    if key not in state["jobs"]:
        raise SystemExit(f"No dev job {name}. Known: {', '.join(state['jobs']) or 'none'}")
    return state["jobs"][key]


def command_revise(args: argparse.Namespace) -> int:
    job = find_job(args.job)
    if args.feedback.strip().lower() == "yes":
        raise SystemExit("Dev runs never publish; approval is a Telegram-only step.")
    if job.get("pipeline") == "v2":
        return run_v2(job, ["revise", "--feedback", args.feedback], args.model)
    if not job.get("session_id"):
        raise SystemExit("This job has no Codex session to resume.")
    return run_turn(job, build_revision_prompt(args.feedback) + DEV_NOTE, resume=True, model=args.model)


def command_show(args: argparse.Namespace) -> int:
    job = find_job(args.job)
    print(json.dumps({key: job[key] for key in ("name", "url", "workflow", "turns", "session_id", "dir")}, indent=2))
    show(job)
    return 0


def command_list(_: argparse.Namespace) -> int:
    for job in load_state()["jobs"].values():
        print(f"{job['name']:8s} {job['workflow']:11s} turns={job['turns']} exit={job.get('last_exit')} {job['url']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=os.getenv("BITS_TODAY_DEV_MODEL", DEFAULT_MODEL),
                        help=f"OpenRouter model for Codex (default {DEFAULT_MODEL}).")
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new", help="Start a job from an X status URL.")
    new.add_argument("url")
    new.add_argument("--workflow", choices=WORKFLOW_TYPES, default="news")
    new.add_argument("--pipeline", choices=("v1", "v2"), default="v1", help="News pipeline to run (v2: news only).")
    new.set_defaults(handler=command_new)
    revise = commands.add_parser("revise", help="Send revision feedback into the job's Codex session.")
    revise.add_argument("job")
    revise.add_argument("feedback")
    revise.set_defaults(handler=command_revise)
    show_parser = commands.add_parser("show", help="Print the latest preview of a job.")
    show_parser.add_argument("job")
    show_parser.set_defaults(handler=command_show)
    commands.add_parser("list", help="List dev jobs.").set_defaults(handler=command_list)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
