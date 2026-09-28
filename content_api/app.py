"""Public read API and authenticated, idempotent publication ingestion."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

import psycopg
import requests
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import Response, RedirectResponse
from PIL import Image
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationError,
    field_validator,
)

from .config import api_key, database_url
from . import instagram_insights, storage

Workflow = Literal["news", "model", "product", "informative", "reel"]
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
app = FastAPI(title="The Bits Today Content API", version="1.0.0")


@app.exception_handler(BotoCoreError)
@app.exception_handler(ClientError)
async def storage_error(_request: Request, _exc: Exception):
    return Response('Media storage temporarily unavailable.', status_code=503)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=300)
    url: HttpUrl | None = None


class AssetInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_type: Literal["x_photo", "generated_background", "bundled_background"]
    origin: Literal["main", "thread", "quote"] | None = None
    source_url: HttpUrl | None = None


class AssetBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assets: list[AssetInfo] = Field(min_length=1, max_length=32)


class Publication(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    external_key: str = Field(min_length=1, max_length=200)
    archive_key: UUID | None = None
    workflow_type: Workflow
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=30000)
    sources: list[Source] = Field(min_length=1, max_length=100)
    published_at: AwareDatetime
    publication_url: HttpUrl | None = None
    is_demo: bool = False
    assets: list[AssetInfo] = Field(default_factory=list, max_length=32)
    video_origin: Literal['original_x'] | None = None

    @field_validator("title", "description")
    @classmethod
    def english_copy(cls, value: str) -> str:
        if any("\u0980" <= char <= "\u09ff" for char in value):
            raise ValueError("Website copy must be English, not bilingual/Bangla.")
        return value


class InstagramPublication(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    archive_key: UUID
    media_id: str = Field(min_length=1, max_length=200)
    permalink: HttpUrl | None = None
    published_at: AwareDatetime | None = None


def connect():
    return psycopg.connect(database_url(), connect_timeout=5, row_factory=dict_row)


def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = f"Bearer {api_key()}"
    if not authorization or not hmac.compare_digest(
        authorization.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(401, "Invalid content API credentials.")


@app.exception_handler(psycopg.Error)
async def database_error(_request: Request, _exc: psycopg.Error):
    # Never include DSNs, credentials, SQL or driver errors in public responses.
    return Response("Content database temporarily unavailable.", status_code=503)


@app.get("/api/health")
def health():
    with connect() as conn:
        conn.execute(
            "SELECT version FROM content_schema_version WHERE version = 1"
        ).fetchone()
    return {"status": "ok"}


def serialize_post(conn, row: dict) -> dict:
    publications = conn.execute(
        """SELECT platform, media_id, permalink, published_at
           FROM content_post_publications WHERE post_id = %s ORDER BY platform""",
        (row["id"],),
    ).fetchall()
    media = conn.execute(
        """SELECT m.sha256, m.mime_type, m.byte_size, m.object_key, pm.position
           FROM content_post_media pm JOIN content_media m USING(sha256)
           WHERE pm.post_id = %s ORDER BY pm.position""",
        (row["id"],),
    ).fetchall()
    source_media = conn.execute(
        """SELECT m.sha256, m.mime_type, m.byte_size, m.object_key, pm.position
           FROM content_post_source_media pm JOIN content_media m USING(sha256)
           WHERE pm.post_id = %s ORDER BY pm.position""",
        (row["id"],),
    ).fetchall()
    assets = conn.execute(
        """SELECT a.position, a.asset_type, a.origin, a.source_url,
                  m.sha256, m.mime_type, m.byte_size, m.object_key
           FROM content_post_assets a JOIN content_media m USING(sha256)
           WHERE a.post_id = %s ORDER BY a.position""",
        (row["id"],),
    ).fetchall()
    return {
        **{key: value for key, value in row.items()
           if key not in {"external_key", "archive_key"}},
        "publications": publications,
        "storage_backend": "r2" if all(item['object_key'] for item in [*media, *source_media, *assets]) else "database",
        "media": [
            {
                **item,
                "kind": "video" if item["mime_type"].startswith("video/") else "image",
                "url": f"/api/media/{item['sha256']}",
                "poster_url": (
                    f"/api/media/{item['sha256']}/poster"
                    if item["mime_type"].startswith("video/")
                    else None
                ),
            }
            for item in media
        ],
        "source_media": [
            {**item, "kind": "image", "url": f"/api/media/{item['sha256']}"}
            for item in source_media
        ],
        "assets": [
            {**item, "url": f"/api/media/{item['sha256']}"} for item in assets
        ],
    }


@app.get("/api/posts")
def list_posts(
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    workflow_type: Workflow | None = None,
    section: Literal["news", "models", "products", "thoughts"] | None = None,
    published_since: AwareDatetime | None = None,
):
    if section and workflow_type:
        raise HTTPException(422, "Use section or workflow_type, not both.")
    section_workflows = {
        "news": ("news", "reel"),
        "models": ("model",),
        "products": ("product",),
        "thoughts": ("informative",),
    }
    with connect() as conn:
        if section:
            where = "WHERE workflow_type = ANY(%s)"
            params = [list(section_workflows[section])]
        else:
            where = "WHERE workflow_type = %s" if workflow_type else ""
            params = [workflow_type] if workflow_type else []
        if published_since:
            where += " AND published_at >= %s" if where else "WHERE published_at >= %s"
            params.append(published_since)
        total = conn.execute(
            f"SELECT count(*) AS count FROM content_posts {where}", params
        ).fetchone()["count"]
        rows = conn.execute(
            f"SELECT * FROM content_posts {where} ORDER BY published_at DESC, id DESC LIMIT %s OFFSET %s",
            [*params, limit, offset],
        ).fetchall()
        return {"items": [serialize_post(conn, row) for row in rows], "total": total}


def _engagement(metrics: dict, baseline: dict | None = None) -> tuple[int, int]:
    def delta(name: str) -> int:
        value = metrics.get(name)
        if value is None:
            return 0
        return max(0, value - ((baseline or {}).get(name) or 0))
    return (delta("likes") + 3 * delta("comments") + 4 * delta("saved")
            + 4 * delta("shares"), delta("views"))


@app.get("/api/posts/rankings")
def post_rankings(
    kind: Literal["trending_week", "popular_all_time"],
    limit: int = Query(6, ge=1, le=20),
):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)
    with connect() as conn:
        candidates = conn.execute(
            """SELECT p.id, p.published_at,
                      latest.views, latest.reach, latest.likes, latest.comments,
                      latest.saved, latest.shares, latest.collected_at,
                      older.views AS old_views, older.likes AS old_likes,
                      older.comments AS old_comments, older.saved AS old_saved,
                      older.shares AS old_shares, older.collected_at AS old_collected_at
               FROM content_posts p
               JOIN content_post_publications pub ON pub.post_id = p.id AND pub.platform = 'instagram'
               JOIN LATERAL (
                   SELECT * FROM content_instagram_insights i
                   WHERE i.post_id = p.id AND i.media_id = pub.media_id
                   ORDER BY i.collected_at DESC LIMIT 1
               ) latest ON true
               LEFT JOIN LATERAL (
                   SELECT * FROM content_instagram_insights i
                   WHERE i.post_id = p.id AND i.media_id = pub.media_id
                     AND i.collected_at <= %s
                   ORDER BY i.collected_at DESC LIMIT 1
               ) older ON true
               WHERE p.workflow_type IN ('news', 'reel') AND NOT p.is_demo""",
            (cutoff,),
        ).fetchall()
        scored = []
        for item in candidates:
            if kind == "trending_week" and item["published_at"] < cutoff and not item["old_collected_at"]:
                continue
            if all(item[name] is None for name in ("views", "likes", "comments", "saved", "shares")):
                continue
            baseline = ({name: item[f"old_{name}"] for name in ("views", "likes", "comments", "saved", "shares")}
                        if kind == "trending_week" and item["published_at"] < cutoff else None)
            score, views = _engagement(item, baseline)
            scored.append((score, views, item["published_at"], item["id"], item["collected_at"]))
        scored.sort(reverse=True)
        results = []
        for score, _, _, post_id, collected_at in scored[:limit]:
            row = conn.execute("SELECT * FROM content_posts WHERE id = %s", (post_id,)).fetchone()
            results.append({**serialize_post(conn, row), "ranking_score": score,
                            "insights_collected_at": collected_at})
        return {"kind": kind, "as_of": now, "total": len(scored), "items": results}


@app.post("/api/internal/instagram-insights/refresh", dependencies=[Depends(authorize)])
def refresh_instagram_insights(
    limit: int = Query(25, ge=1, le=50),
    after: UUID | None = None,
):
    try:
        token, version = instagram_insights.config()
    except instagram_insights.InsightsError as exc:
        raise HTTPException(503, exc.code) from None
    with connect() as lock_conn:
        acquired = lock_conn.execute("SELECT pg_try_advisory_lock(9382714) AS acquired").fetchone()["acquired"]
        if not acquired:
            raise HTTPException(409, "Insight refresh already running.")
        try:
            rows = lock_conn.execute(
                """SELECT post_id, media_id FROM content_post_publications
                   WHERE platform = 'instagram' AND (%s::uuid IS NULL OR post_id > %s::uuid)
                   ORDER BY post_id LIMIT %s""",
                (after, after, limit),
            ).fetchall()
            lock_conn.commit()
            failures = []
            succeeded = 0
            fatal = False
            with requests.Session() as session:
                for row in rows:
                    try:
                        metrics = instagram_insights.fetch_metrics(session, row["media_id"], token, version)
                        collected_at = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
                        with connect() as conn:
                            conn.execute(
                                """INSERT INTO content_instagram_insights
                                   (post_id, media_id, collected_at, views, reach, likes, comments, saved, shares)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                   ON CONFLICT (post_id, collected_at) DO UPDATE SET
                                     media_id=excluded.media_id, views=excluded.views, reach=excluded.reach,
                                     likes=excluded.likes, comments=excluded.comments,
                                     saved=excluded.saved, shares=excluded.shares""",
                                (row["post_id"], row["media_id"], collected_at,
                                 *(metrics[name] for name in instagram_insights.METRICS)),
                            )
                        succeeded += 1
                    except instagram_insights.InsightsError as exc:
                        failures.append({"post_id": row["post_id"], "reason": exc.code})
                        if exc.code in {"rate_limited", "permission_or_token"}:
                            fatal = True
                            break
            return {"processed": succeeded + len(failures), "succeeded": succeeded,
                    "failed": len(failures), "failures": failures,
                    "next_after": rows[-1]["post_id"] if len(rows) == limit and not fatal else None}
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(9382714)")


@app.get("/api/posts/{post_id}")
def get_post(post_id: UUID):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM content_posts WHERE id = %s", (post_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Post not found.")
        return serialize_post(conn, row)


def inspect_media(data: bytes) -> str:
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    try:
        with Image.open(io.BytesIO(data)) as image:
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
                image.format
            )
            image.verify()
        if mime:
            return mime
    except (OSError, ValueError, Image.DecompressionBombError):
        pass
    raise HTTPException(422, "Only valid PNG, JPEG, WebP, or MP4 media is accepted.")


@app.post("/api/posts", dependencies=[Depends(authorize)])
def ingest_post(
    document: Annotated[str, Form(max_length=100000)],
    files: Annotated[list[UploadFile], File()],
    source_files: Annotated[list[UploadFile] | None, File()] = None,
    asset_files: Annotated[list[UploadFile] | None, File()] = None,
):
    try:
        post = Publication.model_validate_json(document)
    except ValidationError:
        raise HTTPException(422, "Invalid publication document.") from None
    if post.external_key == "facebook:":
        raise HTTPException(422, "Facebook publication ID is missing.")
    if not 1 <= len(files) <= 10 or len(source_files or []) > 9:
        raise HTTPException(422, "A post must contain 1–10 ordered media files.")
    asset_infos = post.assets or [
        AssetInfo(asset_type="x_photo", origin="main")
        for _ in source_files or []
    ]
    if len(asset_infos) != len(source_files or []) + len(asset_files or []):
        raise HTTPException(422, "Asset metadata must match asset uploads.")
    if any(item.asset_type != "x_photo" for item in asset_infos[:len(source_files or [])]):
        raise HTTPException(422, "Original photo metadata is out of order.")
    if any(item.asset_type == "x_photo" for item in asset_infos[len(source_files or []):]):
        raise HTTPException(422, "Background metadata is out of order.")
    media, originals, backgrounds, total = [], [], [], 0
    for field, batch in (("media", files), ("originals", source_files or []),
                         ("backgrounds", asset_files or [])):
        for file in batch:
            data = file.file.read(MAX_FILE_BYTES + 1)
            total += len(data)
            if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise HTTPException(413, "Media exceeds the configured upload limit.")
            mime = inspect_media(data)
            if field != "media" and not mime.startswith("image/"):
                raise HTTPException(422, "Post assets must be images.")
            target = {"media": media, "originals": originals,
                      "backgrounds": backgrounds}[field]
            target.append((hashlib.sha256(data).hexdigest(), mime, data))
    with connect() as conn:
        # A publication key identifies one exact immutable delivery, across retries.
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (post.external_key,),
        )
        existing = conn.execute(
            "SELECT * FROM content_posts WHERE external_key = %s", (post.external_key,)
        ).fetchone()
        if existing:
            old = serialize_post(conn, existing)
            same = all(
                str(existing[key]) == str(getattr(post, key))
                for key in ("title", "description", "workflow_type")
            )
            same = (
                same
                and existing["sources"] == json.loads(post.model_dump_json())["sources"]
            )
            same = same and existing["is_demo"] == post.is_demo
            same = same and existing["archive_key"] == post.archive_key
            same = same and [item["sha256"] for item in old["media"]] == [
                item[0] for item in media
            ]
            if not same:
                raise HTTPException(
                    409, "Publication key already exists with different content."
                )
            return {"status": "already_stored", "post": old}
        post_id = uuid4()
        conn.execute(
            """INSERT INTO content_posts(id, external_key, archive_key, workflow_type, title, description, sources,
                        published_at, publication_url, is_demo, video_origin) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                post_id,
                post.external_key,
                post.archive_key,
                post.workflow_type,
                post.title,
                post.description,
                Jsonb(json.loads(post.model_dump_json())["sources"]),
                post.published_at,
                str(post.publication_url) if post.publication_url else None,
                post.is_demo,
                post.video_origin,
            ),
        )
        if post.external_key.startswith("facebook:"):
            conn.execute(
                """INSERT INTO content_post_publications
                   (post_id, platform, media_id, permalink, published_at)
                   VALUES (%s, 'facebook', %s, %s, %s)""",
                (post_id, post.external_key.removeprefix("facebook:"),
                 str(post.publication_url) if post.publication_url else None,
                 post.published_at),
            )
        for index, (digest, mime, data) in enumerate(media):
            storage.save(conn, digest, mime, data)
            conn.execute(
                "INSERT INTO content_post_media VALUES (%s,%s,%s)",
                (post_id, index, digest),
            )
        for index, (digest, mime, data) in enumerate(originals):
            storage.save(conn, digest, mime, data)
            conn.execute(
                "INSERT INTO content_post_source_media VALUES (%s,%s,%s)",
                (post_id, index, digest),
            )
        for position, ((digest, mime, data), info) in enumerate(
            zip([*originals, *backgrounds], asset_infos, strict=True)
        ):
            storage.save(conn, digest, mime, data)
            conn.execute(
                """INSERT INTO content_post_assets
                   (post_id, position, asset_type, origin, source_url, sha256)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (post_id, position, info.asset_type, info.origin,
                 str(info.source_url) if info.source_url else None, digest),
            )
        row = conn.execute(
            "SELECT * FROM content_posts WHERE id = %s", (post_id,)
        ).fetchone()
        return {"status": "stored", "post": serialize_post(conn, row)}


@app.post("/api/publications/instagram", dependencies=[Depends(authorize)])
def attach_instagram_publication(publication: InstagramPublication):
    with connect() as conn:
        post = conn.execute(
            "SELECT id FROM content_posts WHERE archive_key = %s FOR UPDATE",
            (publication.archive_key,),
        ).fetchone()
        if not post:
            raise HTTPException(404, "Website archive is pending for this publication.")
        existing = conn.execute(
            """SELECT media_id, permalink FROM content_post_publications
               WHERE post_id = %s AND platform = 'instagram'""",
            (post["id"],),
        ).fetchone()
        permalink = str(publication.permalink) if publication.permalink else None
        if existing:
            if existing["media_id"] != publication.media_id or (
                existing["permalink"] and permalink
                and existing["permalink"] != permalink
            ):
                raise HTTPException(409, "Instagram publication already linked to another media item.")
            if permalink and not existing["permalink"]:
                conn.execute(
                    """UPDATE content_post_publications SET permalink = %s
                       WHERE post_id = %s AND platform = 'instagram'""",
                    (permalink, post["id"]),
                )
            return {"status": "already_stored", "post_id": post["id"]}
        owner = conn.execute(
            """SELECT post_id FROM content_post_publications
               WHERE platform = 'instagram' AND media_id = %s""",
            (publication.media_id,),
        ).fetchone()
        if owner:
            raise HTTPException(409, "Instagram media item is already linked to another post.")
        conn.execute(
            """INSERT INTO content_post_publications
               (post_id, platform, media_id, permalink, published_at)
               VALUES (%s, 'instagram', %s, %s, COALESCE(%s, now()))""",
            (post["id"], publication.media_id, permalink, publication.published_at),
        )
        return {"status": "stored", "post_id": post["id"]}


@app.post("/api/posts/{post_id}/assets", dependencies=[Depends(authorize)])
def attach_assets(
    post_id: UUID,
    document: Annotated[str, Form(max_length=100000)],
    files: Annotated[list[UploadFile], File()],
):
    try:
        batch = AssetBatch.model_validate_json(document)
    except ValidationError:
        raise HTTPException(422, "Invalid asset metadata.") from None
    if len(files) != len(batch.assets):
        raise HTTPException(422, "Each asset needs one image upload.")
    incoming = []
    total = 0
    for file, info in zip(files, batch.assets, strict=True):
        data = file.file.read(MAX_FILE_BYTES + 1)
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise HTTPException(413, "Assets exceed the configured upload limit.")
        mime = inspect_media(data)
        if not mime.startswith("image/"):
            raise HTTPException(422, "Assets must be images.")
        incoming.append((hashlib.sha256(data).hexdigest(), mime, data, info))
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM content_posts WHERE id = %s FOR UPDATE", (post_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Post not found.")
        existing = serialize_post(conn, row)["assets"]
        next_position = max((item["position"] for item in existing), default=-1) + 1
        source_count = conn.execute(
            "SELECT count(*) AS count FROM content_post_source_media WHERE post_id = %s",
            (post_id,),
        ).fetchone()["count"]
        added = 0
        for digest, mime, data, info in incoming:
            source_url = str(info.source_url) if info.source_url else None
            if any(item["sha256"] == digest and item["asset_type"] == info.asset_type
                   and item["origin"] == info.origin and item["source_url"] == source_url
                   for item in existing):
                continue
            if next_position >= 32 or (info.asset_type == "x_photo" and source_count >= 9):
                raise HTTPException(422, "Post asset limit exceeded.")
            storage.save(conn, digest, mime, data)
            conn.execute(
                """INSERT INTO content_post_assets
                   (post_id, position, asset_type, origin, source_url, sha256)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (post_id, next_position, info.asset_type, info.origin, source_url, digest),
            )
            next_position += 1
            if info.asset_type == "x_photo":
                conn.execute(
                    "INSERT INTO content_post_source_media VALUES (%s,%s,%s)",
                    (post_id, source_count, digest),
                )
                source_count += 1
            existing.append({"sha256": digest, "asset_type": info.asset_type,
                             "origin": info.origin, "source_url": source_url})
            added += 1
        return {"status": "stored" if added else "already_stored", "added": added,
                "post": serialize_post(conn, row)}


@app.put('/api/posts/{post_id}/source-video', dependencies=[Depends(authorize)])
def replace_source_video(post_id: UUID, expected_sha256: Annotated[str, Form()],
                         file: Annotated[UploadFile, File()]):
    data = file.file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, 'Video exceeds upload limit.')
    if inspect_media(data) != 'video/mp4':
        raise HTTPException(422, 'An MP4 source video is required.')
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'source.mp4'
        path.write_bytes(data)
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
            'format=duration', '-of', 'json', str(path)], capture_output=True, timeout=30)
        try:
            duration = float(json.loads(result.stdout)['format']['duration'])
        except (ValueError, KeyError):
            raise HTTPException(422, 'Unable to validate video duration.') from None
        if not 0 < duration <= 180.05:
            raise HTTPException(422, 'Source video must be at most three minutes.')
    digest = hashlib.sha256(data).hexdigest()
    with connect() as conn:
        post = conn.execute('SELECT * FROM content_posts WHERE id=%s FOR UPDATE', (post_id,)).fetchone()
        if not post or post['workflow_type'] != 'reel':
            raise HTTPException(404, 'Reel post not found.')
        current = conn.execute('SELECT sha256 FROM content_post_media WHERE post_id=%s AND position=0',
                               (post_id,)).fetchone()
        if current['sha256'] != digest and current['sha256'] != expected_sha256:
            raise HTTPException(409, 'Published website video changed; refresh before replacing.')
        storage.save(conn, digest, 'video/mp4', data)
        conn.execute('UPDATE content_post_media SET sha256=%s WHERE post_id=%s AND position=0', (digest, post_id))
        conn.execute("UPDATE content_posts SET video_origin='original_x' WHERE id=%s", (post_id,))
        post['video_origin'] = 'original_x'
        return {'status': 'stored', 'post': serialize_post(conn, post)}


@app.get("/api/media/{digest}")
def get_media(
    digest: str, range_header: Annotated[str | None, Header(alias="Range")] = None
):
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise HTTPException(404, "Media not found.")
    with connect() as conn:
        row = conn.execute(
            "SELECT mime_type, byte_size, object_key FROM content_media WHERE sha256 = %s",
            (digest,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Media not found.")
        if row['object_key']:
            return RedirectResponse(storage.url(row['object_key']), status_code=307,
                                    headers={'Cache-Control': 'no-store'})
        size, start, end = row["byte_size"], 0, row["byte_size"] - 1
        headers = {
            "Accept-Ranges": "bytes",
            "ETag": f'"{digest}"',
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        }
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or not any(match.groups()):
                raise HTTPException(416, headers={"Content-Range": f"bytes */{size}"})
            first, last = match.groups()
            start = int(first) if first else max(0, size - int(last))
            end = min(int(last), size - 1) if first and last else size - 1
            if start > end or start >= size:
                raise HTTPException(416, headers={"Content-Range": f"bytes */{size}"})
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        # Fetch only the requested range, important for Reel playback/seeking.
        data = conn.execute(
            "SELECT substring(data FROM %s FOR %s) AS data FROM content_media WHERE sha256 = %s",
            (start + 1, end - start + 1, digest),
        ).fetchone()["data"]
        return Response(
            bytes(data),
            media_type=row["mime_type"],
            headers=headers,
            status_code=206 if range_header else 200,
        )


@lru_cache(maxsize=128)
def render_video_poster(digest: str) -> bytes:
    with connect() as conn:
        row = conn.execute(
            "SELECT mime_type, data, object_key FROM content_media WHERE sha256 = %s", (digest,)
        ).fetchone()
    if not row or row["mime_type"] != "video/mp4":
        raise HTTPException(404, "Video not found.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(503, "Video previews are temporarily unavailable.")
    with tempfile.TemporaryDirectory(prefix="bits-today-poster-") as directory:
        source = Path(directory) / "source.mp4"
        source.write_bytes(storage.read(row['object_key']) if row['object_key'] else bytes(row['data']))
        for seek in ("0.5", "0"):
            try:
                result = subprocess.run(
                    [
                        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-threads", "1", "-i", str(source), "-ss", seek,
                        "-frames:v", "1", "-vf", "scale=960:-2:force_original_aspect_ratio=decrease",
                        "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
                    ],
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                break
            if result.returncode == 0 and result.stdout:
                return result.stdout
    raise HTTPException(503, "Video preview could not be generated.")


@app.get("/api/media/{digest}/poster")
def get_video_poster(digest: str):
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise HTTPException(404, "Video not found.")
    with connect() as conn:
        row = conn.execute('SELECT mime_type, object_key, poster_key FROM content_media WHERE sha256=%s',
                           (digest,)).fetchone()
        if not row or row['mime_type'] != 'video/mp4':
            raise HTTPException(404, 'Video not found.')
        if row['object_key']:
            key = row['poster_key']
            if not key:
                key = storage.put(render_video_poster(digest), 'image/jpeg')
                conn.execute('UPDATE content_media SET poster_key=%s WHERE sha256=%s', (key, digest))
            return RedirectResponse(storage.url(key), status_code=307,
                                    headers={'Cache-Control': 'no-store'})
    return Response(
        render_video_poster(digest),
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
