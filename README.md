# Bits Today post generator

`generate_post.py` turns a tech-news sentence and an approved headline into a
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
python .\generate_post.py `
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

For headline or layout revisions, reuse the saved text-free background instead
of paying for another image generation:

```powershell
python .\generate_post.py `
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

`fetch_tweets.py` accepts one or more status URLs and emits JSON through
[FxTwitter](https://github.com/FxEmbed/FxEmbed), a free MIT-licensed project.
It does not use X's official API, an X developer account, Apify, or an API key.
The fetcher uses FxTwitter v2's thread endpoint so the root post and every
same-author continuation are retained. Pass `--media-dir` to download
photos from the same-author thread and nested quoted posts, and to extract a
JPEG opening frame from attached videos with FFmpeg. These become secondary
post images in source order, capped at nine so the generated graphic keeps the
cross-platform package at ten images total.
At the start of each fetch, the default `--language auto` randomly chooses
English or Bangla once and stores the result as `post_language` in the JSON.
The default `--highlight-style auto` independently chooses a one-line cyan
block, a one-line red block, or the current two-line red-plus-cyan treatment.
Both choices are stored in the JSON and reused instead of being rerolled.

```powershell
python .\fetch_tweets.py `
  "https://x.com/Polymarket/status/2079479742802141202?s=20" `
  --media-dir .\output\polymarket-media `
  --output .\output\polymarket-tweet.json
```

The default public endpoint is `https://api.fxtwitter.com`. You can point the
same script at a self-hosted FxEmbed-compatible endpoint with `--api-base` or
the `FXTWITTER_API_BASE` environment variable.

## Brand downloaded tweet images

`brand_tweet_images.py` creates publishing-ready 1080x1350 copies of downloaded
tweet media. It contains the complete source without cropping or unnecessary
upscaling, lets a `#212121` frame fill the unused 4:5 canvas area, and places the
Bits Today transparent logo in the bottom-right corner. Source files are not
overwritten, and multiple inputs retain the order supplied on the command line.

```powershell
python .\brand_tweet_images.py `
  .\output\tweet-media\123-photo-1.jpg `
  .\output\tweet-media\123-photo-2.jpg `
  --output-dir .\output\tweet-media-branded
```

Use the resulting `*-branded` files as the ordered secondary images in the
Telegram, Facebook, and Instagram stages.

## Generate a news-style description

`generate_description.py` turns validated source text into a bilingual,
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
python .\generate_description.py `
  --tweet-json .\output\polymarket-tweet.json `
  --output .\output\polymarket-description.txt
```

## Editorial approval workflow

1. Send the assistant an X/Twitter status URL.
2. Fetch and validate the post through the free open-source FxTwitter backend;
   its random English/Bangla selection is saved in the tweet JSON.
3. The assistant writes a factual English hook headline from the extracted post.
4. Run `generate_post.py` with that headline and `--tweet-json`. English renders
   unchanged. Bangla triggers one fixed-model translation call and renders the
   translated headline with a Bengali-capable font.
5. Generate the English-plus-Bangla description with `generate_description.py`,
   send it and the complete ordered image set to Telegram, then show the same
   package for review. The generated graphic is first and ordered thread/quote
   photos and video opening frames follow.
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
python .\notify_telegram.py --discover-chat
```

Validate a package without sending it by omitting `--send`. To send the draft
before requesting preview feedback:

```powershell
python .\notify_telegram.py `
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

`publish_facebook.py` first confirms that the configured Page token belongs to
the expected Page. Without `--publish`, it performs validation only:

```powershell
python .\publish_facebook.py `
  --image .\output\approved-post.png `
  --secondary-image .\output\tweet-photo-1.jpg `
  --message-file .\output\approved-description.txt
```

After the user has approved the exact latest preview image and description with `yes`,
publishing requires both safety arguments:

```powershell
python .\publish_facebook.py `
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
python .\publish_instagram.py `
  --image-url "https://public.example/approved-post.png" `
  --secondary-image-url "https://public.example/tweet-photo-1.jpg" `
  --caption-file .\output\approved-description.txt
```

After explicit approval of the exact latest preview, publishing additionally requires:

```powershell
python .\publish_instagram.py `
  --image-url "https://public.example/approved-post.png" `
  --secondary-image-url "https://public.example/tweet-photo-1.jpg" `
  --caption-file .\output\approved-description.txt `
  --publish `
  --confirm yes
```
