"""Durable content delivery after social publication; never republishes social media.

The approved English manifest is prepared with the platform captions. Media is
copied into an outbox before publication. Only a confirmed social ID makes an
entry eligible for API delivery. Failed deliveries can be retried independently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from uuid import UUID

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
WORKFLOWS = {"news", "model", "product", "informative", "reel"}


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        temporary = Path(handle.name)
    temporary.replace(path)


def manifest_path(caption: Path) -> Path:
    return caption.with_suffix(".website.json")


def original_photo_assets(tweet: dict) -> list[dict]:
    """Support both single-tweet metadata and the fetcher's items envelope."""
    photos = []
    seen = set()
    records = [tweet, *tweet.get("items", [])]
    for record in records:
        if not isinstance(record, dict):
            continue
        for item in record.get("downloaded_photos", []):
            if not isinstance(item, dict) or not item.get("local_path"):
                continue
            path = str(Path(item["local_path"]).resolve())
            if path in seen:
                continue
            seen.add(path)
            photos.append({"path": path, "origin": item.get("origin", "main"),
                           "source_url": item.get("source_url")})
    return photos[:9]


def prepare_manifest(
    *, title: str, english: str, tweet_json: Path, description_file: Path
) -> dict:
    tweet = json.loads(tweet_json.read_text(encoding="utf-8"))
    workflow = tweet.get("workflow_type")
    if workflow not in WORKFLOWS:
        raise ValueError("Persist a valid workflow_type in the tweet JSON first.")
    english = "\n".join(
        line for line in english.splitlines() if not line.strip().startswith("#")
    ).strip()
    if (
        not title.strip()
        or not english
        or any("\u0980" <= c <= "\u09ff" for c in title + english)
    ):
        raise ValueError(
            "Website title and description must be non-empty English copy."
        )
    try:
        from .finalize_description import read_tweet_source_urls, source_label_from_url
    except ImportError:
        from finalize_description import read_tweet_source_urls, source_label_from_url
    urls = read_tweet_source_urls(tweet_json)
    sidecar = description_file.with_suffix(".sources.json")
    if sidecar.exists():
        urls.extend(json.loads(sidecar.read_text(encoding="utf-8"))["urls"])
    sources = [
        {"label": source_label_from_url(url), "url": url} for url in dict.fromkeys(urls)
    ]
    # Older descriptions can retain publisher labels even when original URLs are unavailable.
    caption = description_file.read_text(encoding="utf-8")
    if "\n\nSources:\n" in caption:
        for line in caption.rsplit("\n\nSources:\n", 1)[1].splitlines():
            label = line.strip()
            if (
                label
                and not label.startswith("#")
                and label not in {s["label"] for s in sources}
            ):
                sources.append({"label": label, "url": None})
    if not sources:
        raise ValueError("Website archive requires at least one source.")
    original_photos = original_photo_assets(tweet)
    return {
        "archive_key": str(uuid4()),
        "title": title.strip(),
        "description": english,
        "workflow_type": workflow,
        "sources": sources,
        "source_media": original_photos,
    }


def outbox_root() -> Path:
    return Path(
        os.getenv("CONTENT_OUTBOX_DIR") or ROOT / ".automation" / "content-outbox"
    )


def background_assets(media: list[Path]) -> list[dict]:
    """Find raw backgrounds recorded beside the approved rendered cards."""
    selected = []
    seen = set()
    approved = {str(path.resolve()) for path in media}
    sidecars = {
        candidate
        for path in media
        for candidate in (
            path.with_suffix(".json"), path.parent / "post.json",
            path.parent / "carousel.json",
        )
        if candidate.is_file()
    }
    # Legacy news carousels copy the primary image into a child directory but
    # leave its renderer metadata beside the original. Only trust a candidate
    # whose image bytes exactly match an approved item, never a nearby draft.
    for image in media:
        if image.name != "01-headline.png":
            continue
        digest = hashlib.sha256(image.read_bytes()).digest()
        for candidate in image.parent.parent.glob("*.json"):
            original = candidate.with_suffix(".png")
            if (original.is_file() and original.stat().st_size == image.stat().st_size
                    and hashlib.sha256(original.read_bytes()).digest() == digest):
                sidecars.add(candidate)
    for sidecar in sorted(sidecars):
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        declared_media = [
            metadata.get("primary_image"),
            *metadata.get("secondary_images", []),
            *metadata.get("images", []),
        ]
        if sidecar.name in {"post.json", "carousel.json"} and not any(
            path in approved for path in declared_media if path
        ):
            continue
        paths = metadata.get("background_sources", [])
        if metadata.get("background_source") not in (None, "openai-image-api"):
            paths = [metadata["background_source"], *paths]
        saved_background = metadata.get("background_asset_path")
        if saved_background:
            # --keep-background also saves bundled backgrounds; a saved copy
            # alone does not mean the image was AI-generated.
            paths = [saved_background, *metadata.get("background_sources", [])]
        for value in paths:
            path = Path(value).resolve()
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            selected.append({
                "path": path,
                "asset_type": "generated_background" if (
                    value == saved_background
                    and metadata.get("background_source") in (None, "openai-image-api")
                )
                else "bundled_background",
            })
    return selected


def prepare_delivery(
    caption_file: Path | None, media: list[Path], explicit_manifest: Path | None = None
) -> Path | None:
    if not os.getenv("CONTENT_API_URL", "").strip():
        return None
    parsed = urlparse(os.environ["CONTENT_API_URL"])
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("CONTENT_API_URL requires HTTPS outside localhost.")
    if len(os.getenv("CONTENT_API_KEY", "")) < 32:
        raise ValueError("Set CONTENT_API_KEY before publishing.")
    source = explicit_manifest or (
        manifest_path(caption_file) if caption_file else None
    )
    if not source or not source.is_file():
        raise ValueError(
            "Website manifest missing. Run prepare_platform_descriptions.py with --english-title, or pass --website-manifest."
        )
    document = json.loads(source.read_text(encoding="utf-8"))
    caption_hash = document.pop("caption_sha256", None)
    originals = [
        {"path": Path(item["path"] if isinstance(item, dict) else item),
         "origin": item.get("origin", "main") if isinstance(item, dict) else "main",
         "source_url": item.get("source_url") if isinstance(item, dict) else None}
        for item in document.pop("source_media", [])
    ]
    original_paths = [item["path"] for item in originals]
    backgrounds = background_assets(media)
    background_paths = [item["path"] for item in backgrounds]
    if (
        not caption_file
        or not caption_hash
        or hashlib.sha256(caption_file.read_bytes()).hexdigest() != caption_hash
    ):
        raise ValueError(
            "Caption differs from the website manifest. Prepare the final captions again with --english-title."
        )
    if set(document) not in (
        {"title", "description", "workflow_type", "sources"},
        {"archive_key", "title", "description", "workflow_type", "sources"},
    ):
        raise ValueError("Invalid website manifest fields.")
    if "archive_key" in document:
        document["archive_key"] = str(UUID(document["archive_key"]))
    if len(original_paths) > 9 or any(not path.is_file() for path in original_paths):
        raise ValueError("Original X photos are missing or exceed the archive limit.")
    if (
        document["workflow_type"] not in WORKFLOWS
        or not document["title"].strip()
        or not document["description"].strip()
    ):
        raise ValueError("Invalid website manifest copy or workflow.")
    if any(
        "\u0980" <= c <= "\u09ff" for c in document["title"] + document["description"]
    ):
        raise ValueError("Website manifest copy must be English.")
    if not 1 <= len(media) <= 10 or not document["sources"]:
        raise ValueError("Website manifest needs sources and 1–10 media items.")
    all_media = [*media, *original_paths, *background_paths]
    if (
        any(path.stat().st_size > 128 * 1024 * 1024 for path in all_media)
        or sum(path.stat().st_size for path in all_media) > 256 * 1024 * 1024
    ):
        raise ValueError("Website media exceeds the API upload limits.")
    directory = outbox_root() / uuid4().hex
    directory.mkdir(parents=True)
    stored = []
    for index, path in enumerate(media):
        target = directory / f"{index:02d}{path.suffix.lower()}"
        if document['workflow_type'] == 'reel':
            try:
                from .website_video import prepare_source_video
            except ImportError:
                from website_video import prepare_source_video
            prepare_source_video(path, target)
            document['video_origin'] = 'original_x'
        else:
            shutil.copy2(path, target)
        stored.append(
            {
                "path": target.name,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
    stored_originals = []
    for index, item in enumerate(originals):
        path = item["path"]
        target = directory / f"source-{index:02d}{path.suffix.lower()}"
        shutil.copy2(path, target)
        stored_originals.append({
            "path": target.name,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        })
    stored_backgrounds = []
    for index, item in enumerate(backgrounds):
        path = item["path"]
        target = directory / f"background-{index:02d}{path.suffix.lower()}"
        shutil.copy2(path, target)
        stored_backgrounds.append({
            "path": target.name,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        })
    document["assets"] = [
        {"asset_type": "x_photo", "origin": item["origin"],
         "source_url": item["source_url"]}
        for item in originals
    ] + [
        {"asset_type": item["asset_type"], "origin": None, "source_url": None}
        for item in backgrounds
    ]
    receipt = directory / "delivery.json"
    atomic_json(receipt, {"state": "prepared", "document": document,
                          "media": stored, "source_media": stored_originals,
                          "asset_media": stored_backgrounds})
    return receipt


def facebook_publication_url(value: str | None) -> str | None:
    """Graph Reel permalinks may be paths; the website requires absolute URLs."""
    if value and value.startswith("/") and not value.startswith("//"):
        return "https://www.facebook.com" + value
    return value


def confirm_delivery(
    receipt: Path | None, publication_id: str, publication_url: str | None = None
) -> dict:
    if receipt is None:
        return {"status": "disabled"}
    if not publication_id:
        return {"status": "awaiting_publication_id", "receipt": str(receipt)}
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["document"].update(
        external_key=f"facebook:{publication_id}",
        published_at=datetime.now(timezone.utc).isoformat(),
        publication_url=facebook_publication_url(publication_url),
    )
    payload["state"] = "pending"
    # Persist the social success BEFORE API I/O, even if the API is offline.
    atomic_json(receipt, payload)
    return deliver(receipt)


def purge_delivered_copies(receipt: Path, payload: dict) -> None:
    """Remove only outbox copies after R2-backed API delivery is confirmed."""
    if payload.get('state') != 'delivered' or payload.get('storage_backend') != 'r2':
        return
    for field in ('media', 'source_media', 'asset_media'):
        for item in payload.get(field, []):
            path = receipt.parent / item['path']
            if path.resolve().parent != receipt.parent.resolve():
                raise ValueError('Unsafe outbox media path.')
            path.unlink(missing_ok=True)


def deliver(receipt: Path) -> dict:
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    if payload["state"] == "delivered":
        purge_delivered_copies(receipt, payload)
        return {"status": "already_stored", "post_id": payload["post_id"]}
    if payload["state"] != "pending":
        raise ValueError(
            "No confirmed social publication: refusing to archive a draft."
        )
    # Repair previously saved relative Graph permalinks during normal retries.
    # Keep the external key, publication time, approved copy and media unchanged.
    previous_url = payload["document"].get("publication_url")
    normalized_url = facebook_publication_url(previous_url)
    if normalized_url != previous_url:
        payload["document"]["publication_url"] = normalized_url
        atomic_json(receipt, payload)
    try:
        with ExitStack() as stack:
            files = []
            for field, items in (("files", payload["media"]),
                                 ("source_files", payload.get("source_media", [])),
                                 ("asset_files", payload.get("asset_media", []))):
                for item in items:
                    path = receipt.parent / item["path"]
                    if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                        raise ValueError("Outbox media changed after approval.")
                    files.append((field, (path.name,
                                          stack.enter_context(path.open("rb")),
                                          "application/octet-stream")))
            response = requests.post(
                os.environ["CONTENT_API_URL"].rstrip("/") + "/api/posts",
                headers={"Authorization": f"Bearer {os.environ['CONTENT_API_KEY']}"},
                data={"document": json.dumps(payload["document"])},
                files=files,
                timeout=(10, 180),
            )
            if not response.ok:
                raise ValueError(f"Content API HTTP {response.status_code}")
            result = response.json()
            post_id = str(result["post"]["id"])
        payload.update(state="delivered", post_id=post_id,
                       storage_backend=result['post'].get('storage_backend'))
        atomic_json(receipt, payload)
        purge_delivered_copies(receipt, payload)
        return {"status": "stored", "post_id": post_id}
    except Exception:  # noqa: BLE001 - Never turn an archive outage into social republishing.
        # Do not leak credentials or misreport an already successful social post.
        print(
            f'Website delivery pending; retry with content_archive.py --receipt "{receipt}". Do not republish Facebook.',
            file=sys.stderr,
        )
        return {"status": "pending", "receipt": str(receipt)}


def archive_published(
    receipt: Path | None, publication_id: str, publication_url: str | None = None
) -> dict:
    try:
        return confirm_delivery(receipt, publication_id, publication_url)
    except Exception:  # noqa: BLE001 - Preserve confirmed social success on any archive failure.
        print(
            f"Website archive confirmation failed for social ID {publication_id}; receipt: {receipt}. Do not republish Facebook.",
            file=sys.stderr,
        )
        return {
            "status": "confirmation_failed",
            "publication_id": publication_id,
            "receipt": str(receipt),
        }


def instagram_archive_key(caption_file: Path | None) -> str | None:
    """Validate the approved caption's shared identifier before publishing."""
    if not os.getenv("CONTENT_API_URL", "").strip():
        return None
    parsed = urlparse(os.environ["CONTENT_API_URL"])
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("CONTENT_API_URL requires HTTPS outside localhost.")
    if len(os.getenv("CONTENT_API_KEY", "")) < 32:
        raise ValueError("Set CONTENT_API_KEY before publishing.")
    if not caption_file or not caption_file.is_file():
        raise ValueError("An Instagram caption file is required for website mapping.")
    source = manifest_path(caption_file)
    if not source.is_file():
        raise ValueError("Instagram website manifest missing. Prepare platform descriptions first.")
    manifest = json.loads(source.read_text(encoding="utf-8"))
    if manifest.get("caption_sha256") != hashlib.sha256(caption_file.read_bytes()).hexdigest():
        raise ValueError("Instagram caption differs from its website manifest.")
    try:
        return str(UUID(manifest["archive_key"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Instagram website manifest has no valid archive key.") from exc


def deliver_instagram_mapping(receipt: Path) -> dict:
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    if payload["state"] == "delivered":
        return {"status": "already_stored", "post_id": payload["post_id"]}
    if payload["state"] != "pending" or payload.get("type") != "instagram_mapping":
        raise ValueError("Not a pending Instagram mapping receipt.")
    try:
        response = requests.post(
            os.environ["CONTENT_API_URL"].rstrip("/") + "/api/publications/instagram",
            headers={"Authorization": f"Bearer {os.environ['CONTENT_API_KEY']}"},
            json={key: payload[key] for key in
                  ("archive_key", "media_id", "permalink", "published_at")},
            timeout=(10, 30),
        )
        if not response.ok:
            raise ValueError(f"Content API HTTP {response.status_code}")
        post_id = str(response.json()["post_id"])
        payload.update(state="delivered", post_id=post_id)
        atomic_json(receipt, payload)
        return {"status": "stored", "post_id": post_id}
    except Exception:  # noqa: BLE001 - Instagram has already been published.
        print(f'Instagram website mapping pending; retry with content_archive.py --receipt "{receipt}".', file=sys.stderr)
        return {"status": "pending", "receipt": str(receipt)}


def record_instagram_publication(
    archive_key: str | None, media_id: str, permalink: str | None
) -> dict:
    if archive_key is None:
        return {"status": "disabled"}
    if not media_id:
        raise ValueError("Instagram returned no published media ID.")
    receipt = outbox_root() / f"instagram-{UUID(archive_key)}" / "mapping.json"
    if receipt.exists():
        previous = json.loads(receipt.read_text(encoding="utf-8"))
        if previous.get("media_id") != media_id:
            raise ValueError("Another Instagram media ID is already recorded for this archive.")
        if previous["state"] == "delivered":
            return {"status": "already_stored", "post_id": previous["post_id"]}
        if permalink and not previous.get("permalink"):
            previous["permalink"] = permalink
            atomic_json(receipt, previous)
    else:
        atomic_json(receipt, {
            "type": "instagram_mapping", "state": "pending",
            "archive_key": archive_key, "media_id": media_id,
            "permalink": permalink,
            "published_at": datetime.now(timezone.utc).isoformat(),
        })
    return deliver_instagram_mapping(receipt)


def archive_instagram_published(
    archive_key: str | None, media_id: str, permalink: str | None
) -> dict:
    try:
        return record_instagram_publication(archive_key, media_id, permalink)
    except Exception:  # noqa: BLE001 - Never misreport a published Instagram post.
        print(
            f"Instagram published media {media_id}, but its website mapping could not be saved. Reconcile this media ID without republishing.",
            file=sys.stderr,
        )
        return {"status": "confirmation_failed", "media_id": media_id}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--retry-pending", action="store_true")
    args = parser.parse_args()
    paths = ([args.receipt] if args.receipt else
             [*sorted(outbox_root().glob("*/delivery.json")),
              *sorted(outbox_root().glob("instagram-*/mapping.json"))]
             if args.retry_pending else [])
    failed = False
    for path in paths:
        if json.loads(path.read_text(encoding="utf-8"))["state"] == "prepared":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = (deliver_instagram_mapping(path) if payload.get("type") == "instagram_mapping"
                  else deliver(path))
        print(json.dumps(result))
        failed |= result["status"] == "pending"
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
