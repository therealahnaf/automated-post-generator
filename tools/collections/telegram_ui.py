"""Telegram buttons and replies for collections, called by tools/news/telegram_codex_watcher.py.

Flow: an X link's workflow selector has a Collect button -> pick a recent collection or New collection (reply
with a name) -> Save for later / Save and build -> build asks Media / Tweets / Media + Tweets and queues a normal
watcher job (workflow "collection") whose preview, revisions and `yes` work like any other post.
/m (also /menu, /dashboard, /start or /collections) opens the dashboard: one message listing open collections.
Tapping a collection turns it, in place, into that collection's actions: View (a numbered contact sheet; reply
"drop 3", "move 5 to 1" or "rename New name"), build as Media / Tweets / Media + Tweets, Archive, and Back.

Every function takes the watcher module as `w`, so this file has no import cycle with it.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from tools.collections import slides, store

ROOT = Path(__file__).resolve().parents[2]
CALLBACK = re.compile(r"(collect|into|newcol|save|build|colview|colbuild|colarchive|dash):[\w:]+")
# The dashboard command: one short letter to type, plus the longer names and Telegram's own /start.
COMMAND = re.compile(r"^/(?:m|menu|dashboard|start|collections)(?:@\w+)?\s*$", re.I)
DASHBOARD_LIMIT = 12
COLLECT_TIMEOUT = 300

PROMPTS = """
CREATE TABLE IF NOT EXISTS collection_prompts (
    message_id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('name', 'sheet')),
    job_id INTEGER,
    collection_id INTEGER,
    created_at TEXT NOT NULL
);
"""


def handles(data: str) -> bool:
    return CALLBACK.fullmatch(data) is not None


def remember(connection: sqlite3.Connection, w, message_id: int, kind: str, *, job_id: int | None = None,
             collection_id: int | None = None) -> None:
    connection.executescript(PROMPTS)
    connection.execute("INSERT OR REPLACE INTO collection_prompts VALUES (?, ?, ?, ?, ?)",
                       (message_id, kind, job_id, collection_id, w.utc_now()))
    connection.commit()


def prompt_for(connection: sqlite3.Connection, message_id: int) -> sqlite3.Row | None:
    connection.executescript(PROMPTS)
    return connection.execute("SELECT * FROM collection_prompts WHERE message_id = ?", (message_id,)).fetchone()


# ------------------------------------------------------------------------------------------------ keyboards
def picker_keyboard(job_id: int) -> dict[str, Any]:
    connection = store.connect()
    rows = [[{"text": f"{collection.name} ({store.count(connection, collection.id)})",
              "callback_data": f"into:{job_id}:{collection.id}"}] for collection in store.recent(connection)]
    rows.append([{"text": "+ New collection", "callback_data": f"newcol:{job_id}"}])
    rows.append([{"text": "Cancel", "callback_data": f"workflow:{job_id}:cancel"}])
    return {"inline_keyboard": rows}


def save_keyboard(job_id: int, collection_id: int) -> dict[str, Any]:
    return {"inline_keyboard": [
        [{"text": "Save for later", "callback_data": f"save:{job_id}:{collection_id}:later"},
         {"text": "Save and build", "callback_data": f"save:{job_id}:{collection_id}:build"}],
        [{"text": "Back", "callback_data": f"collect:{job_id}"}],
    ]}


def type_keyboard(collection_id: int) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": slides.TYPE_LABELS[kind], "callback_data": f"build:{collection_id}:{kind}"}]
                                for kind in slides.TYPES]}


def list_view() -> tuple[str, dict[str, Any]]:
    """The dashboard: every open collection as one button, newest activity first."""
    connection = store.connect()
    collections = store.recent(connection, limit=DASHBOARD_LIMIT)
    lines = ["The Bits Today · Dashboard", "", "Collections"]
    rows = []
    if not collections:
        lines.append("None yet. Tap Collect under any X link to start one.")
    for collection in collections:
        size = store.count(connection, collection.id)
        rows.append([{"text": f"{collection.name[:28]} · {size} post{'s' if size != 1 else ''}",
                      "callback_data": f"dash:col:{collection.id}"}])
    if collections:
        lines.append("Tap one to view, build or archive it.")
    rows.append([{"text": "Refresh", "callback_data": "dash:home"}])
    return "\n".join(lines), {"inline_keyboard": rows}


def collection_view(collection_id: int) -> tuple[str, dict[str, Any]]:
    """One collection's actions, shown in place of the dashboard list."""
    connection = store.connect()
    collection = store.get(connection, collection_id)
    size = store.count(connection, collection_id)
    text = (f"The Bits Today · Dashboard\n\n{collection.name} · {size} post{'s' if size != 1 else ''}\n"
            "View shows the posts as a numbered sheet. Build makes the carousel as:")
    rows = [[{"text": "View posts", "callback_data": f"colview:{collection_id}"}],
            [{"text": slides.TYPE_LABELS[kind], "callback_data": f"build:{collection_id}:{kind}"} for kind in slides.TYPES],
            [{"text": "Archive", "callback_data": f"colarchive:{collection_id}"},
             {"text": "‹ Back", "callback_data": "dash:home"}]]
    return text, {"inline_keyboard": rows}


# ------------------------------------------------------------------------------------------------ actions
def pending_link_job(connection: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    """The X link's job, while it still waits for a workflow choice."""
    return connection.execute("SELECT * FROM jobs WHERE id = ? AND status = 'queued' AND workflow_type IS NULL",
                              (job_id,)).fetchone()


def run_collect(url: str, collection_id: int) -> dict:
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "collections" / "collect.py"), "--url", url,
                             "--collection", str(collection_id)], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=COLLECT_TIMEOUT)
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        payload = {"error": (result.stderr.strip() or "The collector printed nothing.")[-300:]}
    if result.returncode != 0 or "error" in payload:
        raise RuntimeError(payload.get("error") or "Could not collect the post.")
    return payload


def finish_link_job(connection: sqlite3.Connection, w, job_id: int, note: str) -> None:
    connection.execute("""UPDATE jobs SET status = 'completed', workflow_type = 'collection', final_output = ?,
                          finished_at = ?, updated_at = ? WHERE id = ? AND status = 'queued'""",
                       (note, w.utc_now(), w.utc_now(), job_id))
    connection.commit()


def send_sheet(w, session, config, connection: sqlite3.Connection, collection_id: int, reply_to: int) -> None:
    collection_connection = store.connect()
    collection = store.get(collection_connection, collection_id)
    items = store.items(collection_connection, collection_id)
    if not items:
        w.send_text(session, config, f"{collection.name} is empty.", reply_to_message_id=reply_to)
        return
    with tempfile.TemporaryDirectory() as folder:
        sheet = slides.contact_sheet(items, collection.name, Path(folder) / "sheet.jpg")
        caption = (f"{collection.name} · {len(items)} posts\n"
                   "Reply to this sheet with: drop 3 · move 5 to 1 · rename New name")
        with sheet.open("rb") as handle:
            response = session.post(w.telegram_url(config, "sendPhoto"), timeout=(10, 120),
                                    data={"chat_id": config.telegram_chat_id, "caption": caption,
                                          "reply_markup": json.dumps(type_keyboard(collection_id)),
                                          "reply_parameters": json.dumps({"message_id": reply_to,
                                                                          "allow_sending_without_reply": True})},
                                    files={"photo": ("sheet.jpg", handle, "image/jpeg")})
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram sendPhoto failed: {payload.get('description', 'unknown error')}")
    remember(connection, w, int(payload["result"]["message_id"]), "sheet", collection_id=collection_id)


def start_build(w, session, config, connection: sqlite3.Connection, callback, collection_id: int, kind: str) -> None:
    """Queue the collection post as a normal watcher job, with its own progress dashboard."""
    collection = store.get(store.connect(), collection_id)
    size = store.count(store.connect(), collection_id)
    ids = w.send_text(session, config,
                      f"The Bits Today · Collection post\n{collection.name} · {slides.TYPE_LABELS[kind]} · {size} posts\n\n"
                      "Queued to generate.", reply_to_message_id=callback.message_id, reply_markup={"inline_keyboard": []})
    if not ids:
        raise RuntimeError("Telegram returned no dashboard message.")
    message = w.TelegramMessage(update_id=callback.update_id, message_id=ids[0], chat_id=callback.chat_id,
                                sender_id=callback.sender_id, text=f"collection:{collection_id}:{kind}",
                                reply_to_message_id=None, received_at=w.utc_now())
    job_id = w.enqueue_request(connection, message, workflow_type="collection")
    w.set_progress_message(connection, job_id, ids[0])


def handle_callback(w, session, config, connection: sqlite3.Connection, callback) -> None:
    action, _, rest = callback.data.partition(":")
    parts = rest.split(":")
    answer = lambda text="": w.answer_callback(session, config, callback.callback_id, text)  # noqa: E731
    try:
        if action == "dash":
            # Dashboard navigation edits the same message: the list, or one collection's actions.
            if parts[0] == "col":
                collection_id = int(parts[1])
                if store.get(store.connect(), collection_id).status != "open":
                    answer("That collection is no longer open.")
                    text, keyboard = list_view()
                else:
                    answer()
                    text, keyboard = collection_view(collection_id)
            else:
                answer("Refreshed.")
                text, keyboard = list_view()
            w.record_event(connection, callback, "dashboard_" + parts[0], None)
            w.edit_text(session, config, callback.message_id, text, reply_markup=keyboard)
            return
        if action in ("collect", "into", "newcol", "save"):
            job_id = int(parts[0])
            job = pending_link_job(connection, job_id)
            if job is None:
                w.record_event(connection, callback, "ignored_stale_callback", job_id)
                answer("This link has already been handled.")
                return
            if action == "collect":
                w.record_event(connection, callback, "collect_picker", job_id)
                answer("Choose a collection.")
                w.edit_text(session, config, callback.message_id,
                            f"The Bits Today · Job {job_id}\n\nAdd this post to which collection?",
                            reply_markup=picker_keyboard(job_id))
            elif action == "into":
                collection = store.get(store.connect(), int(parts[1]))
                w.record_event(connection, callback, "collect_target", job_id)
                answer(collection.name)
                w.edit_text(session, config, callback.message_id,
                            f"The Bits Today · Job {job_id}\n\nAdd to {collection.name}.",
                            reply_markup=save_keyboard(job_id, collection.id))
            elif action == "newcol":
                w.record_event(connection, callback, "collect_new", job_id)
                answer("Reply with a name.")
                ids = w.send_text(session, config, "Reply to this message with a name for the new collection.",
                                  reply_to_message_id=callback.message_id,
                                  reply_markup={"force_reply": True, "input_field_placeholder": "Collection name"})
                for message_id in ids:
                    remember(connection, w, message_id, "name", job_id=job_id)
            else:
                collection_id, mode = int(parts[1]), parts[2]
                collection = store.get(store.connect(), collection_id)
                w.record_event(connection, callback, f"collect_{mode}", job_id)
                answer("Saving…")
                w.edit_text(session, config, callback.message_id,
                            f"The Bits Today · Job {job_id}\n\nSaving to {collection.name}…")
                try:
                    result = run_collect(str(job["request_text"]), collection_id)
                except (RuntimeError, subprocess.TimeoutExpired) as exc:
                    w.edit_text(session, config, callback.message_id,
                                f"The Bits Today · Job {job_id}\n\nCould not save the post: {exc}",
                                reply_markup=picker_keyboard(job_id))
                    return
                media = []
                if result["photos"]:
                    media.append(f"{result['photos']} photo{'s' if result['photos'] != 1 else ''}")
                if result["videos"]:
                    media.append(f"{result['videos']} video{'s' if result['videos'] != 1 else ''}")
                note = (f"Saved to {collection.name}: {result['count']} posts"
                        + (f" (this one with {' and '.join(media)})" if media else "") + ".")
                finish_link_job(connection, w, job_id, note)
                w.edit_text(session, config, callback.message_id, f"The Bits Today · Job {job_id}\n\n{note}"
                            + ("\n\nBuild it as:" if mode == "build" else ""),
                            reply_markup=type_keyboard(collection_id) if mode == "build" else None)
            return
        collection_id = int(parts[0])
        collection = store.get(store.connect(), collection_id)
        if action == "build":
            kind = parts[1]
            w.record_event(connection, callback, "collection_build", None)
            if store.count(store.connect(), collection_id) == 0:
                answer("This collection is empty.")
                return
            answer(f"Building {slides.TYPE_LABELS[kind]}…")
            start_build(w, session, config, connection, callback, collection_id, kind)
        elif action == "colview":
            w.record_event(connection, callback, "collection_view", None)
            answer(collection.name)
            send_sheet(w, session, config, connection, collection_id, callback.message_id)
        elif action == "colbuild":
            w.record_event(connection, callback, "collection_build_menu", None)
            answer("Choose the type.")
            w.send_text(session, config, f"Build {collection.name} as:", reply_to_message_id=callback.message_id,
                        reply_markup=type_keyboard(collection_id))
        elif action == "colarchive":
            store.set_status(store.connect(), collection_id, "archived")
            w.record_event(connection, callback, "collection_archived", None)
            answer(f"Archived {collection.name}.")
            text, keyboard = list_view()
            w.edit_text(session, config, callback.message_id, text, reply_markup=keyboard)
    except (ValueError, RuntimeError) as exc:
        w.record_event(connection, callback, "collection_error", None)
        answer(str(exc)[:190])


def handle_reply(w, session, config, connection: sqlite3.Connection, message) -> bool:
    """Replies to a name prompt or a contact sheet; False when the reply is for something else."""
    if message.reply_to_message_id is None:
        return False
    prompt = prompt_for(connection, message.reply_to_message_id)
    if prompt is None:
        return False
    collection_connection = store.connect()
    try:
        if prompt["kind"] == "name":
            job_id = int(prompt["job_id"])
            if pending_link_job(connection, job_id) is None:
                w.record_event(connection, message, "ignored_stale_reply", job_id)
                w.send_text(session, config, "That link has already been handled.", reply_to_message_id=message.message_id)
                return True
            collection = store.create(collection_connection, message.text)
            w.record_event(connection, message, "collection_created", job_id)
            w.send_text(session, config, f"Created {collection.name}. Add this post to it:",
                        reply_to_message_id=message.message_id, reply_markup=save_keyboard(job_id, collection.id))
            return True
        collection_id = int(prompt["collection_id"])
        text = message.text.strip()
        drop = re.fullmatch(r"(?:drop|remove)\s+([\d,\sand]+)", text, re.I)
        move = re.fullmatch(r"move\s+(\d+)\s+to\s+(\d+)", text, re.I)
        rename = re.fullmatch(r"rename\s+(.+)", text, re.I | re.S)
        if drop:
            numbers = sorted({int(value) for value in re.findall(r"\d+", drop.group(1))}, reverse=True)
            store.remove(collection_connection, collection_id, numbers)
        elif move:
            store.move(collection_connection, collection_id, int(move.group(1)), int(move.group(2)))
        elif rename:
            store.rename(collection_connection, collection_id, rename.group(1))
        else:
            w.record_event(connection, message, "collection_unknown_reply", None)
            w.send_text(session, config, "Reply with drop 3, move 5 to 1, or rename New name.",
                        reply_to_message_id=message.message_id)
            return True
        w.record_event(connection, message, "collection_edited", None)
        send_sheet(w, session, config, connection, collection_id, message.message_id)
    except (ValueError, RuntimeError) as exc:
        w.record_event(connection, message, "collection_error", None)
        w.send_text(session, config, str(exc), reply_to_message_id=message.message_id)
    return True


def show_list(w, session, config, connection: sqlite3.Connection, message) -> None:
    """Send the dashboard (the /m command)."""
    w.record_event(connection, message, "dashboard_opened", None)
    text, keyboard = list_view()
    w.send_text(session, config, text, reply_to_message_id=message.message_id, reply_markup=keyboard)
