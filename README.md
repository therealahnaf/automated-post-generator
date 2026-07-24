# Bits Today post generator

`tools/news/generate_post.py` turns a tech-news sentence and an approved headline into a
1080×1350 social post:

1. The operator supplies a reviewed headline with `--headline`.
2. OpenAI Image API creates a text-free editorial background.
3. Pillow crops the image and adds the dark news-style gradient, branded
   headline, `Bits Today | <date>` byline, and bottom-right logo.

The image model never renders the headline. All typography is added
programmatically by Pillow. The palette uses coral `#FF5757` and mint
`#C2FFE1`; the current design has no bottom footer, extra badges, or
AI-generated credit line. English headlines and bylines use bundled Roboto;
Bangla headlines retain their Bengali-capable font.

## Tool layout

Post workflows are grouped by content type under `tools/`. The current news
workflow lives in `tools/news/`; future post formats can use sibling directories
without mixing their scripts into the news pipeline. Shared repository assets,
fonts, credentials, policies, and tests remain at the repository root.
`AGENTS.md` is the lightweight X-link router and shared approval/publishing
contract. It dispatches validated stories to either
[`tools/news/WORKFLOW.md`](tools/news/WORKFLOW.md) or
[`tools/models/WORKFLOW.md`](tools/models/WORKFLOW.md), and trusted manual reel
requests to [`tools/reels/WORKFLOW.md`](tools/reels/WORKFLOW.md).

The model-announcement workflow lives in `tools/models/`. It creates a centered
primary card with a large `Meet`, the model name, and `by <company name>`,
followed by feature-focused secondary cards. Posts
with photos create exactly one description segment per photo; posts without
photos split the finalized English description into two or three cards that
reuse the primary background. See
[`tools/models/WORKFLOW.md`](tools/models/WORKFLOW.md) for the complete flow.

The reel workflow converts an X video into a maximum 59.5-second 1080x1920
H.264/AAC post. It contains the source video over a blurred moving fill, applies
the news headline treatment, and ends with the live-video coral/mint type-out
outro. `tools/reels/generate_reel.py` renders it; the dedicated Facebook and
Instagram reel publishers retain the same exact-approval safeguards. Instagram
fetches the approved local MP4 from a stable, content-hashed HTTPS endpoint
rather than consuming Facebook's separately transcoded CDN copy.

Three Pillow presets are available through `--style`:

- `brand-block`: bold sans-serif blocks with an italic final line.
- `editorial-italic`: serif editorial typography and italic mint emphasis.
- `split-signal`: display type with alternating bold-italic word accents.

## Setup

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:OPENAI_API_KEY = "your-key-here"
```

Do not put the API key in this repository or pass it as a command-line argument.

## Run

```powershell
python .\tools\news\generate_post.py `
  "NEW: China’s Z.AI begins operating a 1-gigawatt AI data center built entirely with domestic chips — enough power for roughly 750,000 homes." `
  --headline "China’s Z.AI Opens 1-Gigawatt Data Center Powered by Domestic Chips" `
  --style brand-block `
  --output .\output\china-ai-data-center.png `
  --keep-background
```

The default image model is `gpt-image-2` at medium quality. Override it with
`--image-model` and `--image-quality` if needed. The script also writes a JSON
sidecar containing the supplied title, image prompt, image model, and source
sentence.

When `--tweet-json` contains downloaded photos, the first photo is automatically
added uncropped to the main post in a rounded frame when it is at least 640x480.
Photos are never enlarged; smaller photos remain secondary media only. Use
`--feature-image` to choose a specific photo, or `--no-feature-image` to disable
the inset. The generated JSON sidecar records the exact `feature_image_source`
used so downstream carousel preparation can remove that image from the
secondary set.

For headline or layout revisions, reuse the saved text-free background instead
of paying for another image generation:

```powershell
python .\tools\news\generate_post.py `
  "The source tweet text" `
  --headline "The revised headline" `
  --background-input .\output\post-background.png `
  --output .\output\post-revised.png
```

## Test the deterministic layout code

```powershell
python -m unittest discover -s tests -v
```

These tests do not call the API or spend credits.

## Fetch X/Twitter post data

`tools/news/fetch_tweets.py` accepts one or more status URLs and emits JSON through
[FxTwitter](https://github.com/FxEmbed/FxEmbed), a free MIT-licensed project.
It does not use X's official API, an X developer account, Apify, or an API key.
The fetcher uses FxTwitter v2's thread endpoint so the root post and every
same-author continuation are retained. Pass `--media-dir` to download photos
from the same-author thread and nested quoted posts. Videos are not downloaded
and video frames are not extracted. Photos become secondary post images in
source order, capped at nine so the generated graphic keeps the cross-platform
package at ten images total.
At the start of each fetch, the default `--language auto` randomly chooses
English or Bangla once and stores the result as `post_language` in the JSON.
The default `--highlight-style auto` independently chooses a one-line cyan
block, a one-line red block, or the current two-line red-plus-cyan treatment.
Both choices are stored in the JSON and reused instead of being rerolled.

```powershell
python .\tools\news\fetch_tweets.py `
  "https://x.com/Polymarket/status/2079479742802141202?s=20" `
  --media-dir .\output\polymarket-media `
  --output .\output\polymarket-tweet.json
```

The default public endpoint is `https://api.fxtwitter.com`. You can point the
same script at a self-hosted FxEmbed-compatible endpoint with `--api-base` or
the `FXTWITTER_API_BASE` environment variable.

## Brand downloaded tweet images

`tools/news/brand_tweet_images.py` creates publishing-ready 1080x1350 copies of downloaded
tweet media. It contains the complete source without cropping or unnecessary
upscaling, lets a `#212121` frame fill the unused 4:5 canvas area, and places the
Bits Today transparent logo in the bottom-right corner. Source files are not
overwritten, and multiple inputs retain the order supplied on the command line.

```powershell
python .\tools\news\brand_tweet_images.py `
  .\output\tweet-media\123-photo-1.jpg `
  .\output\tweet-media\123-photo-2.jpg `
  --post-metadata .\output\post.json `
  --output-dir .\output\tweet-media-branded
```

Use the resulting `*-branded` files as the ordered secondary images in the
Telegram, Facebook, and Instagram stages. When `--post-metadata` identifies a
tweet photo embedded in the generated primary, that photo is skipped and only
the other source images are produced. If the primary contains no tweet photo,
all supplied images remain secondary.

## Generate a news-style description

`tools/news/generate_description.py` turns validated source text into a bilingual,
high-stakes social description. The first model call writes the English news
copy. A second model call translates and summarizes that copy into concise
Bangla while preserving names, numbers, attribution, and uncertainty. The
script uses the fixed `gpt-5.6-luna` model for both calls; the model is not
configurable through `.env` or CLI arguments. For an English-selected post, the
output order is:

```text
English description

---

বাংলা অনুবাদ-সারাংশ
```

For a Bangla-selected post, the same sections are reversed: Bangla first, then
`---`, then English.

The English prompt uses few-shot examples for paragraphing and attribution,
but both prompts prohibit unsupported facts and completed truncated clauses.
The combined output is capped at 2,200 characters for Instagram compatibility.

```powershell
python .\tools\news\generate_description.py `
  --tweet-json .\output\polymarket-tweet.json `
  --output .\output\polymarket-description.txt
```

After web research, append the supplied X URL and each research URL actually
used in the final copy. The X URL is read from the tweet JSON and stays first:

```powershell
python .\tools\news\finalize_description.py `
  --description-file .\output\polymarket-description.txt `
  --tweet-json .\output\polymarket-tweet.json `
  --source-url "https://example.com/research-used-in-the-copy" `
  --output .\output\polymarket-description.txt
```

The final caption ends with `Sources:` followed by one deduplicated URL per
line. The formatter rejects output exceeding Instagram's 2,200-character
caption limit instead of silently truncating the description.

## Editorial approval workflow

1. Send the assistant an X/Twitter status URL.
2. Fetch and validate the post through the free open-source FxTwitter backend;
   its random English/Bangla selection is saved in the tweet JSON.
3. The assistant writes a factual English hook headline from the extracted post.
4. Run `tools/news/generate_post.py` with that headline and `--tweet-json`. English renders
   unchanged. Bangla triggers one fixed-model translation call and renders the
   translated headline with a Bengali-capable font.
5. Generate the English-plus-Bangla description with
   `tools/news/generate_description.py`,
   send it and the complete ordered image set to Telegram, then show the same
   package for review. The generated graphic is first and ordered thread/quote
   photos follow.
   Revise it until the user says `yes`.
6. Publish to Facebook and Instagram only after the user explicitly says `yes`
   for the exact latest preview package.

The Facebook publisher is guarded by both conversational approval and explicit
command-line confirmation so validation cannot accidentally publish a post.

## Send a review package to Telegram

Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to `.env`. A new bot cannot
discover your private chat until you open it in Telegram and send `/start`.
After doing that, find the available chat ID with:

```powershell
python .\tools\news\notify_telegram.py --discover-chat
```

Validate a package without sending it by omitting `--send`. To send the draft
before requesting preview feedback:

```powershell
python .\tools\news\notify_telegram.py `
  --image .\output\draft-post.png `
  --secondary-image .\output\tweet-photo-1.jpg `
  --description-file .\output\draft-description.txt `
  --stage preview `
  --send
```

Repeat `--secondary-image` for additional media images. Every materially revised
image or description must be resent with `--stage preview --send` before
requesting approval again. The script sends the generated main image first,
then source images in order, then the full description as separate messages.

## Validate or publish to Facebook

`tools/news/publish_facebook.py` first confirms that the configured Page token belongs to
the expected Page. Without `--publish`, it performs validation only:

```powershell
python .\tools\news\publish_facebook.py `
  --image .\output\approved-post.png `
  --secondary-image .\output\tweet-photo-1.jpg `
  --message-file .\output\approved-description.txt
```

After the user has approved the exact latest preview image and description with `yes`,
publishing requires both safety arguments:

```powershell
python .\tools\news\publish_facebook.py `
  --image .\output\approved-post.png `
  --secondary-image .\output\tweet-photo-1.jpg `
  --message-file .\output\approved-description.txt `
  --publish `
  --confirm yes
```

Repeat `--secondary-image` for more attached photos. With multiple images, the
publisher creates one ordered Facebook multi-photo post and returns
`facebook_image_urls` for the Instagram stage. Facebook credentials are loaded
from `.env` and are never supplied on the command line.

## Validate or publish to Instagram

Instagram Login publishing uses the configured `@bits_t0day` Business account.
Meta requires public HTTPS image URLs, so the approved workflow publishes the
ordered images to Facebook first and reuses their Facebook-hosted URLs for an
Instagram carousel. The generated graphic remains the first carousel item.

Without `--publish`, this validates the account and publishing quota only:

```powershell
python .\tools\news\publish_instagram.py `
  --image-url "https://public.example/approved-post.png" `
  --secondary-image-url "https://public.example/tweet-photo-1.jpg" `
  --caption-file .\output\approved-description.txt
```

After explicit approval of the exact latest preview, publishing additionally requires:

```powershell
python .\tools\news\publish_instagram.py `
  --image-url "https://public.example/approved-post.png" `
  --secondary-image-url "https://public.example/tweet-photo-1.jpg" `
  --caption-file .\output\approved-description.txt `
  --publish `
  --confirm yes
```

## Telegram-to-Codex hourly queue (VPS)

`tools/news/telegram_codex_queue.py` polls the configured private Telegram chat, stores
new text messages in a durable SQLite FIFO queue, and runs one job per hour.
Messages are deduplicated by both Telegram update ID and chat/message ID. The
runner passes each prompt to `codex exec` over standard input with the required
`Read AGENTS.md` prefix and unattended-publishing suffix.

Install or refresh the managed root crontab entry:

```bash
./.venv/bin/python tools/news/telegram_codex_queue.py --install-cron
```

Installation deliberately skips all Telegram history already waiting at that
moment. New messages are picked up at minute 7 of each hour. A file lock avoids
overlapping jobs; failures are not automatically retried because a job may have
partially published before failing.

```bash
./.venv/bin/python tools/news/telegram_codex_queue.py --status
./.venv/bin/python tools/news/telegram_codex_queue.py --retry JOB_ID
```

Queue state and logs live under ignored `.automation/`. Set the optional
`TELEGRAM_ALLOWED_USER_IDS` in `.env` to a comma-separated sender allowlist for
additional protection. `CODEX_QUEUE_CRON_MINUTE` changes the hourly minute at
installation time.

## Telegram-to-Codex approval watcher (VPS)

`tools/news/telegram_codex_watcher.py` is a separate, always-running alternative to the
hourly queue. It uses Telegram long polling, keeps each Codex session, and
sends previews as replies to the originating Telegram request. Sending an X
status URL first presents `News`, `Model Release`, `Reel`, `Auto Detect`, and
`Cancel` inline buttons. `/news URL`, `/model URL`, `/reel URL`, and `/auto URL`
are direct-selection shortcuts. Manual selections are authoritative; Auto
Detect runs the news/model classifier.

The selector message becomes one edited progress dashboard for source fetching,
media discovery, headline, research, bilingual description, generated items,
preview, revisions, and both publishing stages. Replies to this dashboard never
approve or revise a job. Reply exactly
`yes` to any message in the latest preview package to resume that same Codex
session and publish. A non-`yes` reply is treated as revision feedback and must
produce another preview before approval. Replies to stale previews and
unthreaded `yes` messages are rejected.

The watcher has its own SQLite database under `.automation/watcher/`. On first
startup it imports the existing hourly queue's Telegram offset, so messages are
neither replayed nor skipped while switching processes. Interrupted publishing
is marked failed and never retried automatically.

Pause only the managed cron entry, then install the systemd service:

```bash
./.venv/bin/python tools/news/telegram_codex_watcher.py --pause-cron
sudo install -m 0644 systemd/bitstoday-telegram-watcher.service \
  /etc/systemd/system/bitstoday-telegram-watcher.service
sudo systemctl daemon-reload
sudo systemctl enable --now bitstoday-telegram-watcher.service
```

Inspect the durable job state and live service logs with:

```bash
./.venv/bin/python tools/news/telegram_codex_watcher.py --status
sudo systemctl status bitstoday-telegram-watcher.service
sudo journalctl -u bitstoday-telegram-watcher.service -f
```

The original hourly implementation remains available. Stop and disable the
watcher, then run `tools/news/telegram_codex_queue.py --install-cron` to restore it.
