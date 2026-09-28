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
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

import psycopg
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
from . import storage

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
        **{key: value for key, value in row.items() if key != "external_key"},
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
        total = conn.execute(
            f"SELECT count(*) AS count FROM content_posts {where}", params
        ).fetchone()["count"]
        rows = conn.execute(
            f"SELECT * FROM content_posts {where} ORDER BY published_at DESC, id DESC LIMIT %s OFFSET %s",
            [*params, limit, offset],
        ).fetchall()
        return {"items": [serialize_post(conn, row) for row in rows], "total": total}


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
            """INSERT INTO content_posts(id, external_key, workflow_type, title, description, sources,
                        published_at, publication_url, is_demo, video_origin) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                post_id,
                post.external_key,
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
