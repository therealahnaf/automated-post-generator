# Narrated story videos

Isolated API feature: X URL → tagged story → segment narration and illustrations
→ local landscape MP4 and thumbnail. Titles are supplied by the user in Telegram,
not generated. Informative Telegram
requests keep the existing Facebook/Instagram carousel and add this as a separate
YouTube branch. Its own preview, Accept, then manual title reply authorize only
YouTube. Accept alone never queues upload. Website archival and R2 are not used
for these local video artifacts.

## Run

Use the existing backend environment in one terminal:

```sh
python -m pip install -r content_api/requirements.txt
python -m uvicorn content_api.app:app --host 127.0.0.1 --port 8001
```

Start the worker in a **second terminal**, with the same environment/output dir:

```sh
python -m content_api.story_video.worker
```

For Windows, substitute `.venv\Scripts\python.exe` for `python`. To process at most
one queued job, use `python -m content_api.story_video.worker --once`.
FFmpeg and FFprobe must be on PATH on Windows or Linux, with libx264 and AAC
encoders. Install the updated requirements (including `google-auth`). No
PostgreSQL migrations are required: private job/review tables migrate additively
in local SQLite. Old jobs without preview opt-in are not automatically sent or published.

Start a **third terminal** for outgoing Telegram previews and approved YouTube
uploads, so a long generation cannot block approvals:

```sh
python -m content_api.story_video.delivery_worker
```

This worker also accepts `--once`. Keep the existing Telegram watcher running;
it is the only `getUpdates` consumer. Do not start another bot poller locally
while the production watcher is active. API and both workers must share the
same output directory and credentials. The systemd templates under `systemd/`
are for the Linux VM; installing them is a deployment step, not automatic.
The production website stack instead runs these two workers as Compose
services sharing the API image and a persistent private bind mount. Do not
run both the host service templates and Compose workers at the same time.

Configuration uses the existing environment/root `.env`/`content_api/.env`:

- `OPENROUTER_API_KEY`: required, never accepted in request bodies or returned.
- `CONTENT_API_KEY`: existing bearer credential for every endpoint below.
- `STORY_VIDEO_OUTPUT_DIR`: optional; defaults to `output/story-videos`. Relative
  values resolve against the repository root.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ALLOWED_USER_IDS`: preview
  destination and allowed approvers. Group chats require an explicit user list;
  a private chat can default to its owner. Use the same settings as the watcher.
- `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`,
  `YOUTUBE_CHANNEL_ID`: existing saved channel OAuth authorization. Upload and
  read-only scopes are required; no API key/password or new browser login for
  each upload. The channel is checked before preview and before upload.
- `STORY_VIDEO_API_URL`: watcher/interactive CLI only; optional internal base URL,
  falling back to `CONTENT_API_URL`. Prefer `http://127.0.0.1:8001` (substitute the
  actual API port) on the same VM. Remote URLs must use HTTPS.

Model IDs, voice, prompt paths and style reference are server-owned. Never expose
the content API key to a public frontend.

## Create a job

### Standalone thumbnail generation

The reusable packaging service is now included in video jobs. It also remains
available independently without generating narration or video:

```sh
python -m content_api.story_video.generate_thumbnail https://x.com/waiting4_asi/status/2106090345230635176 --output-dir output/story-thumbnails/example
```

It uses the existing fetcher and OpenRouter credentials, making only one Qwen
2K/16:9 image request attaching **both** original thumbnail references and the
tweet context. No title/concept text-model request is made. It saves
`thumbnail.png` (1280×720), the original generated image, prompts,
source context, reference snapshots and `packaging.json` usage/status metadata.
No title is printed on the artwork. Edit `prompts/thumbnail.md` independently of
narration/frame prompts. The title-generation prompt, model setting and old
`generate_title_thumbnail` CLI have been removed. User titles have no AI keyword
or word-count restriction and are never automatically rewritten.
Existing output directories are refused; failed paid calls are not retried.
This command does not regenerate a story, audio, video, or publish/upload anything.

### Video job endpoint

`POST /api/internal/story-videos`

Send `Authorization: Bearer <CONTENT_API_KEY>` and `Idempotency-Key: <unique-key>`.
Keys accept 1–128 letters, digits, dots, hyphens, underscores or colons. Reuse the
same key when retrying an HTTP request to avoid duplicate billable work.

```json
{
  "x_url": "https://x.com/waiting4_asi/status/2106090345230635176",
  "speech_speed": 1.3,
  "instructions": "Keep the narration thoughtful and conversational.",
  "send_telegram_preview": true,
  "telegram_reply_to_message_id": 123,
  "youtube_privacy": "public"
}
```

Only `x_url` is required. Speed defaults to **1.3**, must be finite and between
0.5 and 2.0. Optional instructions are trusted writing directions from an
authenticated operator. Fetched text is always in a separate untrusted source
message. Arbitrary URLs, local paths, model IDs and API keys are not accepted.
Telegram preview defaults to true. For standalone local artifact generation,
set `send_telegram_preview: false` (no YouTube/Telegram credentials needed).
Visibility defaults to public and is shown in the approval message; private and
unlisted are also supported. An unverified Google API project may restrict
uploads to private regardless of the requested visibility.

Returns **202**, a `Location` status URL and a job object:

```json
{
  "id": "a11fbb24-f01a-4eb3-bab7-083b2211d93f",
  "status": "queued",
  "stage": "queued",
  "created_at": "2026-10-23T00:00:00Z",
  "updated_at": "2026-10-23T00:00:00Z",
  "completed_segments": 0,
  "total_segments": 0,
  "error_code": null,
  "status_url": "/api/internal/story-videos/a11fbb24-f01a-4eb3-bab7-083b2211d93f",
  "video_url": null,
  "manifest_url": null,
  "duration_seconds": null
}
```

Submission queues work, rather than waiting for models or FFmpeg. Repeated keys
with identical canonical input reuse the same job, including completed/failed
jobs. Conflicting reuse returns **409**. At most 20 queued/running jobs are
accepted; excess submissions return **429**. Missing provider configuration,
prompts, reference or FFmpeg returns **503** before queuing. Different keys
intentionally create different billable generations.

## Progress and downloads

All routes require the same bearer header:

- `GET /api/internal/story-videos/{job_id}`: status and progress.
- `GET /api/internal/story-videos/{job_id}/video`: MP4 with byte-range support.
- `GET /api/internal/story-videos/{job_id}/preview`: first 30 seconds, H.264/AAC.
- `GET /api/internal/story-videos/{job_id}/thumbnail`: 1280×720 thumbnail PNG.
- `GET /api/internal/story-videos/{job_id}/manifest`: ordered segments, source
  boundaries, sample counts, start/end timing, asset paths, usage and prompt hashes.

Statuses: `queued`, `running`, `completed`, `failed`.
Stages: `queued`, `starting`, `fetching`, `story`, `packaging`, `audio`, `images`, `segments`,
`rendering`, `completed`, `failed`, `interrupted`.
Completed-segment count tracks finished audio-and-image pairs. Total count becomes
available after segmentation. Poll every few seconds. Completed jobs include
download URLs and measured duration; failures contain safe error codes, never
provider bodies or credentials. Downloads return **409** until completed and
**404** for missing jobs/artifacts. These are private, not public media-host URLs.
Status also includes `title`, `thumbnail_url`, `preview_url`, `review_status`,
`review_error`, and (after upload) `youtube_id` / `youtube_url`. Generation status
and review status are independent: `completed` does not mean published.

## Telegram approval and publishing

1. Selecting Informative queues the carousel as before. At claim time, the
   watcher records a separate durable YouTube outbox entry. Queue HTTP retries
   reuse `informative-youtube:<chat-id>:<source-message-id>`; they cannot duplicate
   paid video generation. No other workflow queues a companion.
2. After generation, the delivery worker checks the configured channel and sends
   the thumbnail, first 30 seconds, and complete final YouTube description.
   A final message identifies the channel and visibility and has **Accept video**
   and **Cancel**. Every component must arrive before the
   buttons become active. Only the full video is uploaded to YouTube.
3. The existing watcher forwards `tft:` button callbacks to the authenticated
   `POST /api/internal/story-videos/{job_id}/decision` endpoint. It validates the
   allowed sender, chat, exact message, nonce, current state, and package hashes.
   Typed `yes` and carousel approval/cancellation never approve/cancel YouTube.
4. Accept atomically queues a **title request**, not an upload. The delivery worker
   sends a new message asking the accepting user to reply directly with a title,
   with a Cancel button. The watcher forwards that reply to authenticated
   `POST /api/internal/story-videos/title-reply` with `chat_id`, `sender_id`,
   `message_id`, `reply_to_message_id`, and `text`. The API verifies the stored
   prompt ID, original approver, chat, active state and unchanged media. A valid
   title reply queues publication using the exact wording (outer whitespace is
   trimmed). Titles must be one line of 1–100 characters, without `<`, `>` or
   control characters. Invalid replies leave the request open for correction.
   Unrelated messages, carousel `yes`, and titles sent to other messages never
   authorize upload. Duplicate replies/buttons cannot queue another upload or
   replace an accepted title. Cancel retains local files but prevents publication. Any
   changes to title, thumbnail, description, video, preview, channel, or privacy
   invalidate the approved package before upload.
5. The upload is resumable. Session URI and returned video ID are checkpointed
   in the private SQLite database, never in public status or artifact manifests.
   The video ID is saved before setting the custom thumbnail. If that fails,
   status is `published_thumbnail_failed` with the existing URL; do not upload
   again. The channel may need custom-thumbnail eligibility enabled.

Review states: `pending`, `delivering`, `awaiting_approval`, `title_pending`,
`prompting_title`, `awaiting_title`, `title_prompt_failed`, `approved`,
`publishing`, `published`, `cancelled`, `preview_failed`, `publication_failed`,
`published_thumbnail_failed`; jobs without previews use `not_requested`.

Authenticated `POST /api/internal/story-videos/{job_id}/retry-delivery` explicitly
retries a failed preview (new nonce and new approval), a failed title prompt
(still requires the user's title reply), or an already approved
failed upload using its existing session/video ID. It never reapproves cancelled
jobs, regenerates media, or creates another upload when an old session may exist.
Expired/uncertain sessions fail closed for operator review. No automatic retry
of unknown YouTube writes is performed. YouTube and Telegram errors are reported
separately from the unaffected carousel.

The additive SQLite migration preserves existing jobs and uploads. Older
generated-title approvals with no upload started are sent a fresh preview and
require manual titles. Already-started uploads retain their original title/hash
and resumable checkpoint; they never start a duplicate upload. Waiting for a
title survives API and worker restarts. No PostgreSQL schema change is needed.

For an interactive informative request (not a watcher turn):

```sh
python tools/thoughts/queue_youtube.py https://x.com/example/status/123 --idempotency-key informative-demo-123
```

Do not use this command again from watcher Codex turns: the watcher already
queued the companion. All three backend processes and the updated existing
watcher must be deployed together. Restarting only the API is insufficient.

## Pipeline and timing

1. The existing X fetcher and source assembler retrieve the validated post,
   same-author thread and quoted text.
2. `deepseek/deepseek-v4-flash-0731` generates the English story with emotion tags.
   Low reasoning and a 12,288-token output budget reserve room for the long
   narration instead of letting the model's default high reasoning consume it.
3. The parser splits at delivery/emotion boundaries and restores active directions
   and balanced paired tags for each independent speech request.
   The shared packaging service asks Qwen directly for a 2K landscape thumbnail
   grounded in the same fetched context, attaching both original
   thumbnail style references; it does not fetch X a second time.
4. `fish-audio/drama-3` generates each narrated segment using the Adrian voice and
   exact provider speed multiplier.
5. `qwen/qwen-image-2.1-turbo` creates a distinct **16:9, 1K** illustration for
   every narrated segment, attaching only the original `assets/style-reference.png`.
   Generated frames are never references for subsequent frames.
6. FFmpeg decodes clips into 48 kHz mono PCM. Actual sample counts set segment
   durations and the concatenated voice track. Images change at narration
   boundaries, rounded to video-frame precision—not estimated word counts.
7. The final file is **1280×720, 24 fps, H.264/AAC**, with MP4 fast-start. There
   are no crossfades or background music. The transparent Tokens For Thought logo
   is overlaid throughout: 80 px wide, with 24 px bottom/right padding. Transparent
   outer margins are trimmed before scaling; no background box is added.

Standalone tag-only beats are carried into the next spoken segment (trailing
cues into the last one), avoiding empty clips and flashing silent frames. Every
original boundary remains in the manifest. Per-segment synthesis provides exact
timing, but vocal continuity may vary slightly versus a single full-story request.

The story prompt requests at least 1,000 spoken words. The service checks the
actual count before spending on audio/images. A 3,000-word upper bound and
80-narrated-segment limit guard against accidental oversized bills. An invalid
or truncated story fails the job without an automatic paid retry.

## Local output and prompts

Each job saves under `output/story-videos/{job_id}/`:

```text
source.json              source URL and assembled thread/quote context
story.txt                full tagged narration
manifest.json            segments, assets and measured timing
style-reference.png      original reference snapshot
brand-logo.png           exact logo snapshot used by the renderer
prompts/                 template snapshots and per-segment prompts
audio/                   original MP3 clips and decoded PCM WAV clips
images/                  one generated PNG per narrated segment
narration.wav            concatenated voice track
frames.ffconcat          image timing used by FFmpeg
video.mp4                final video, exposed only after verification
preview.mp4              first 30 seconds for Telegram, never the uploaded video
description.txt          final YouTube description
packaging/thumbnail.png  final 1280×720 thumbnail
packaging/               prompts, both references and packaging usage metadata
```

Edit `prompts/story.md`, `prompts/image_first.md` and `prompts/image_next.md`.
The experimental story/image CLIs now read these same canonical files, including
the user's storytelling example and 1,000-word instruction. Every job snapshots
its prompts, reference and logo for reproducibility. No API credentials enter artifacts.
Changed speed/logo defaults apply to new jobs only; existing videos are not rewritten.
The final user-supplied title and its Telegram reply ID are stored in the private
review row in `jobs.sqlite3` and exposed as `title` in authenticated job status,
not synthesized into a generated artifact.

## Reliability and limits

Queue state is in `jobs.sqlite3` beside job directories, using WAL transactions
and atomic worker claims. Queued jobs survive API/worker restarts. Workers renew
running-job leases every 15 seconds. After 120 seconds without renewal, a running
job becomes failed/interrupted on the next queue access; it is **not requeued**.
This avoids repeating a potentially charged request after a timeout/crash.

There are finite provider timeouts, no automatic paid retries/model fallbacks,
and no automatic generation-resume endpoint. Partial assets are retained for operator review.
Review a failure before submitting another key. Failed jobs keep their error and
do not masquerade as successful results.

API and worker must share **local disk on one machine**; this is not a multi-host
or network-filesystem queue. One worker handles one job at a time. Additional
explicitly started workers can claim different jobs safely. No worker is started
implicitly on API import, Uvicorn reload, or web-worker startup.

Files stay local indefinitely for now; monitor disk usage and keep them private.
A job makes one story request and one thumbnail request,
plus one audio and one image request per narrated segment. Tests mock paid calls,
Telegram and YouTube writes, and render real FFmpeg videos from fixtures:

```sh
python -m unittest tests.test_story_video tests.test_story_video_packaging tests.test_story_video_review tests.test_youtube_companion tests.test_telegram_codex_watcher -v
```
