"""Collections of X posts, kept for later aggregate posts.

One SQLite file and one folder per item under the collections root (default .automation/collections, override
with BITS_TODAY_COLLECTIONS_DIR). Each item folder holds the fetched tweet.json, its photos in media/ and its
videos in videos/, saved when the post is collected, since viral posts are often deleted later.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'posted', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL REFERENCES collections(id),
    tweet_id TEXT NOT NULL,
    url TEXT NOT NULL,
    position INTEGER NOT NULL,
    added_at TEXT NOT NULL,
    removed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS items_by_collection ON items(collection_id, removed, position);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def root() -> Path:
    configured = os.getenv("BITS_TODAY_COLLECTIONS_DIR", "").strip()
    return Path(configured) if configured else ROOT / ".automation" / "collections"


@dataclass(frozen=True)
class Item:
    id: int
    collection_id: int
    tweet_id: str
    url: str
    position: int
    folder: Path

    @property
    def tweet_json(self) -> Path:
        return self.folder / "tweet.json"

    def status(self) -> dict:
        return (json.loads(self.tweet_json.read_text(encoding="utf-8-sig")).get("items") or [{}])[0]

    def videos(self) -> list[Path]:
        return sorted((self.folder / "videos").glob("*.mp4")) if (self.folder / "videos").is_dir() else []


@dataclass(frozen=True)
class Collection:
    id: int
    name: str
    status: str
    updated_at: str


def connect() -> sqlite3.Connection:
    folder = root()
    folder.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(folder / "collections.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def item_folder(collection_id: int, item_id: int) -> Path:
    return root() / f"collection-{collection_id}" / f"item-{item_id}"


def create(connection: sqlite3.Connection, name: str) -> Collection:
    name = " ".join(name.split())[:60]
    if not name:
        raise ValueError("A collection needs a name.")
    cursor = connection.execute("INSERT INTO collections(name, created_at, updated_at) VALUES (?, ?, ?)",
                                (name, now(), now()))
    connection.commit()
    return get(connection, int(cursor.lastrowid))


def get(connection: sqlite3.Connection, collection_id: int) -> Collection:
    row = connection.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
    if row is None:
        raise ValueError(f"Collection {collection_id} does not exist.")
    return Collection(int(row["id"]), row["name"], row["status"], row["updated_at"])


def recent(connection: sqlite3.Connection, limit: int = 6) -> list[Collection]:
    rows = connection.execute("SELECT * FROM collections WHERE status = 'open' ORDER BY updated_at DESC, id DESC LIMIT ?",
                              (limit,)).fetchall()
    return [Collection(int(row["id"]), row["name"], row["status"], row["updated_at"]) for row in rows]


def items(connection: sqlite3.Connection, collection_id: int) -> list[Item]:
    rows = connection.execute("SELECT * FROM items WHERE collection_id = ? AND removed = 0 ORDER BY position, id",
                              (collection_id,)).fetchall()
    return [Item(int(row["id"]), collection_id, row["tweet_id"], row["url"], index,
                 item_folder(collection_id, int(row["id"]))) for index, row in enumerate(rows, start=1)]


def count(connection: sqlite3.Connection, collection_id: int) -> int:
    return len(items(connection, collection_id))


def touch(connection: sqlite3.Connection, collection_id: int) -> None:
    connection.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (now(), collection_id))
    connection.commit()


def has_tweet(connection: sqlite3.Connection, collection_id: int, tweet_id: str) -> bool:
    return connection.execute("SELECT 1 FROM items WHERE collection_id = ? AND tweet_id = ? AND removed = 0",
                              (collection_id, tweet_id)).fetchone() is not None


def add(connection: sqlite3.Connection, collection_id: int, tweet_id: str, url: str, fetched: Path) -> Item:
    """Record a collected post; `fetched` is the folder holding its tweet.json, media/ and videos/."""
    get(connection, collection_id)
    if has_tweet(connection, collection_id, tweet_id):
        raise ValueError("That post is already in this collection.")
    last = connection.execute("SELECT COALESCE(MAX(position), 0) FROM items WHERE collection_id = ?",
                              (collection_id,)).fetchone()[0]
    cursor = connection.execute("INSERT INTO items(collection_id, tweet_id, url, position, added_at) VALUES (?, ?, ?, ?, ?)",
                                (collection_id, tweet_id, url, int(last) + 1, now()))
    item_id = int(cursor.lastrowid)
    target = item_folder(collection_id, item_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(fetched), str(target))
    connection.execute("UPDATE collections SET updated_at = ?, status = 'open' WHERE id = ?", (now(), collection_id))
    connection.commit()
    return next(item for item in items(connection, collection_id) if item.id == item_id)


def remove(connection: sqlite3.Connection, collection_id: int, numbers: list[int]) -> list[int]:
    """Drop items by their shown number (1-based); returns the numbers removed."""
    current = items(connection, collection_id)
    dropped = [number for number in numbers if 1 <= number <= len(current)]
    for number in dropped:
        connection.execute("UPDATE items SET removed = 1 WHERE id = ?", (current[number - 1].id,))
    touch(connection, collection_id)
    return dropped


def undo_last(connection: sqlite3.Connection, collection_id: int, item_id: int) -> None:
    connection.execute("UPDATE items SET removed = 1 WHERE id = ? AND collection_id = ?", (item_id, collection_id))
    touch(connection, collection_id)


def move(connection: sqlite3.Connection, collection_id: int, number: int, to: int) -> None:
    current = items(connection, collection_id)
    if not (1 <= number <= len(current) and 1 <= to <= len(current)):
        raise ValueError(f"Use numbers from 1 to {len(current)}.")
    order = [item.id for item in current]
    order.insert(to - 1, order.pop(number - 1))
    for position, item_id in enumerate(order, start=1):
        connection.execute("UPDATE items SET position = ? WHERE id = ?", (position, item_id))
    touch(connection, collection_id)


def rename(connection: sqlite3.Connection, collection_id: int, name: str) -> None:
    name = " ".join(name.split())[:60]
    if not name:
        raise ValueError("A collection needs a name.")
    connection.execute("UPDATE collections SET name = ?, updated_at = ? WHERE id = ?", (name, now(), collection_id))
    connection.commit()


def set_status(connection: sqlite3.Connection, collection_id: int, status: str) -> None:
    connection.execute("UPDATE collections SET status = ?, updated_at = ? WHERE id = ?", (status, now(), collection_id))
    connection.commit()
