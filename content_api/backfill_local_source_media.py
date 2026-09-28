"""Add preserved X photos to one local demo post without changing published media.

Usage: python -m content_api.backfill_local_source_media local-demo:twitch-ai photo1.jpg photo2.jpg
"""

from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from PIL import Image

from .config import database_url
from . import storage


def backfill(external_key: str, photos: list[Path]) -> int:
    if urlparse(database_url()).hostname not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("Demo backfill is restricted to the local database.")
    if not external_key.startswith("local-demo:") or not 1 <= len(photos) <= 9:
        raise ValueError("Choose one local demo post and 1–9 original photos.")
    media = []
    for path in photos:
        data = path.read_bytes()
        if len(data) > 20 * 1024 * 1024:
            raise ValueError(f"Original photo is too large: {path}")
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            mime = Image.MIME.get(image.format or "")
        if mime not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError(f"Unsupported original photo: {path}")
        media.append((hashlib.sha256(data).hexdigest(), mime, data))
    with psycopg.connect(database_url(), connect_timeout=5) as conn:
        row = conn.execute(
            "SELECT id, is_demo FROM content_posts WHERE external_key = %s FOR UPDATE",
            (external_key,),
        ).fetchone()
        if not row or not row[1]:
            raise ValueError("Local demo post not found.")
        if conn.execute(
            "SELECT 1 FROM content_post_source_media WHERE post_id = %s LIMIT 1",
            (row[0],),
        ).fetchone():
            raise ValueError("This post already has archived original photos.")
        for position, (digest, mime, data) in enumerate(media):
            storage.save(conn, digest, mime, data)
            conn.execute(
                "INSERT INTO content_post_source_media VALUES (%s,%s,%s)",
                (row[0], position, digest),
            )
    return len(media)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("external_key")
    parser.add_argument("photos", type=Path, nargs="+")
    args = parser.parse_args()
    print(f"Archived {backfill(args.external_key, args.photos)} original photos.")


if __name__ == "__main__":
    main()
