# The Bits Today content API

FastAPI + PostgreSQL metadata, with all durable media in private Cloudflare R2
objects. Media is content-hash deduplicated and immutable. Stable API media URLs
redirect browsers to one-hour signed R2 URLs; bytes do not stream through the VM.
Source URLs remain separate from caption-safe labels. Video posters also live in R2.

Configure `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and
`R2_BUCKET` in the root or API `.env`; never expose them as frontend variables.
Run `python -m content_api.migrate` then `python -m content_api.migrate_to_r2`
to migrate legacy blobs. The resumable migration downloads and verifies each
uploaded object's SHA-256 before clearing its database bytes. Back up first.

Website reels use the original tweet video referenced by the approved reel's
render metadata, not the rendered social reel. Clips over 180 seconds are trimmed
to 180 seconds and encoded as browser-compatible H.264/AAC MP4 with fast-start.
Short compatible originals are remuxed without re-encoding. Social publishing is
unchanged. The publisher retains local outbox copies until R2-backed delivery is
confirmed, then removes those copies but keeps the receipt. Working files for
ongoing social workflows and explicit rollback backups are not automatically erased.

## Run locally / deploy on Linux

From the repository root, with Python 3.12+:

```sh
python -m pip install -r content_api/requirements.txt
# Copy content_api/.env.example to content_api/.env, then set real credentials.
python -m content_api.migrate
python -m uvicorn content_api.app:app --host 127.0.0.1 --port 8001
```

`CONTENT_DATABASE_URL` belongs to `content_api/.env`. `CONTENT_API_KEY` is a
random secret of at least 32 characters, shared with the root publishing `.env`.
The publisher additionally needs `CONTENT_API_URL` (locally
`http://127.0.0.1:8001`). Empty URL disables archival without changing social
behavior. `CONTENT_OUTBOX_DIR` optionally overrides `.automation/content-outbox`.
Never put these credentials in `VITE_*` variables or the browser.

For this workstation only, `python -m content_api.bootstrap_local` creates an
isolated `bits_today` database and restricted login on the existing `pg` container
at port **15432**. It refuses to overwrite existing database/role configuration.
It never restarts containers or changes the existing application database.
Production setup uses the same API code with environment-configured PostgreSQL;
the optional Docker bootstrap is not required on the VM.

`python -m content_api.seed_local` explicitly imports the existing frontend story
fixtures and an existing reel for local testing. These seven records are marked
`is_demo`; they are not claims of new social publication. This does not backfill
the entire historical archive. The live frontend has no hardcoded story fallback.

## Endpoints

- `GET /api/health`: database/schema readiness.
- `GET /api/posts?limit=30&offset=0&workflow_type=news`: latest first, bounded
  pagination and optional `news|model|product|informative|reel` filter.
- `GET /api/posts?section=news&limit=6&offset=0`: website sections. `news`
  includes news and reels; `models`, `products`, and `thoughts` map to their
  matching workflows. Use either `section` or `workflow_type`, not both.
- `GET /api/posts/{uuid}`: English copy, sources, type, date and ordered media.
- `GET /api/media/{sha256}`: signed R2 redirect; R2 serves bytes and MP4 ranges.
- `GET /api/media/{sha256}/poster`: cached JPEG preview frame for stored MP4s;
  requires FFmpeg on the API host. The list API includes `poster_url` for videos.
- `POST /api/posts`: Bearer-authenticated multipart ingestion, `document` JSON
  plus repeated `files` fields in display order. Optional repeated
  `source_files` fields preserve original X photos separately, in source order.
  No frontend write access.

Publication document fields: `external_key`, `workflow_type`, `title`,
`description`, `sources: [{label, url}]`, `published_at` (timezone required),
optional `publication_url` and `is_demo`. Identical retries reuse the existing
record; reusing a key with different copy/media returns 409. One transaction
stores all media and the post. No partial post appears in the feed.

The API also returns `assets`, an ordered list of image inputs associated with
the post. Each asset has a type (`x_photo`, `generated_background`, or
`bundled_background`), a media URL, and optional X origin (`main`, `thread`, or
`quote`) and source URL. The carousel or original tweet video remains in `media`; asset
uploads never replace it. `source_media` remains the ordered original-photo
view used by the detail-page carousel. Background blobs are content-addressed
and deduplicated across posts. An authenticated
`POST /api/posts/{post_id}/assets` accepts a JSON `document` containing
`assets: [{asset_type, origin?, source_url?}]` and one repeated `files` upload
per entry; it is safe to retry identical uploads without republishing.

Limits: 10 published media items and 9 original X photos, 128 MiB per item,
256 MiB total. Images must decode as
PNG/JPEG/WebP. MP4 uploads require the ISO media signature; publication scripts
also run their existing video validation. This API is for trusted publishers,
not arbitrary public uploads. Use a reverse proxy with HTTPS, a 260 MiB request
limit, write-endpoint rate limiting, and a request timeout of at least 180 seconds.
Keep the database private and back up its metadata. Media bytes live in R2;
database backups alone cannot restore objects deleted from the bucket.

## Publication integration

1. `finalize_description.py --output` also preserves the real research URLs in
   a `.sources.json` sidecar. Social captions still show labels, not raw URLs.
2. Run `prepare_platform_descriptions.py` with the normal arguments **and
   `--english-title "Approved English headline"`**. This produces a
   `.website.json` beside each caption, using the English section regardless of
   the selected platform language. Informative posts use the English hook.
3. Approval remains unchanged. Facebook publishers automatically locate the
   caption-adjacent manifest; `--website-manifest` permits an explicit path.
   The caption hash must match the final approved file. Inline-only descriptions
   need to be saved to a caption file first when archival is enabled.
4. Before any social write, approved media, downloaded X photos, and raw
   backgrounds recorded by the renderers are copied to a durable local outbox.
   The archive collector reads existing render metadata automatically; the
   step-by-step social workflow does not gain an extra manual action. News
   image-model backgrounds are now saved beside the rendered card so they can
   be archived as separate assets.
   After a confirmed Facebook publication, its ID is checkpointed and delivery
   is attempted. All image workflows share this publisher; reels use the same
   hook. Instagram and host-only calls do not duplicate database records.
5. API failure does not pretend Facebook failed. Inspect `website_archive` in
   publisher output. Retry ONLY archival, never the successful social post:

```sh
python tools/news/content_archive.py --receipt .automation/content-outbox/JOB/delivery.json
python tools/news/content_archive.py --retry-pending
```

Retry receipts contain no API keys. Copies remain available until R2 delivery
is confirmed, then only those outbox copies are removed. `prepared` entries without a confirmed social
ID cannot be replayed. If a process dies between social success and local
checkpointing, reconcile the social result manually; never blindly republish.
Production runs the retry command through its archive service timer.

Image packages retain approved social media separately from source assets.
Website reels use verified original tweet videos, capped at three minutes.
Titles and descriptions are always English; social packages are unchanged.

## Frontend and tests

Vite proxies `/api` to localhost:8001 (override `CONTENT_API_PROXY` for dev).
Production should serve the static React build and reverse-proxy `/api` to
FastAPI on the **same origin**. No permissive CORS configuration is needed.

```sh
python -m unittest tests.test_content_api tests.test_content_archive tests.test_publish_facebook tests.test_prepare_platform_descriptions tests.test_finalize_description -v
```

API integration tests create and drop their own randomly named schema in the
configured database. They never truncate published content. No tests contact
Facebook, Instagram, OpenAI, or Telegram.
