# Bits Today post generator

`generate_post.py` turns a tech-news sentence and an approved headline into a
1080×1350 social post:

1. The operator supplies a reviewed headline with `--headline`.
2. OpenAI Image API creates a text-free editorial background.
3. Pillow crops the image and adds the dark news-style gradient, highlighted
   headline, and `Bits Today` byline.

The image model never renders the headline. All typography is added
programmatically by Pillow. The current design has no bottom footer, badges, or
AI-generated credit line.

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
The implementation follows the same single-tweet backend selected by
[x-tweet-fetcher](https://github.com/ythx-101/x-tweet-fetcher).
When FxTwitter appears to return a long-post preview, the script validates a
matching VxTwitter-compatible response and uses its longer text if available.

```powershell
python .\fetch_tweets.py `
  "https://x.com/Polymarket/status/2079479742802141202?s=20" `
  --output .\output\polymarket-tweet.json
```

The default public endpoint is `https://api.fxtwitter.com`. You can point the
same script at a self-hosted FxEmbed-compatible endpoint with `--api-base` or
the `FXTWITTER_API_BASE` environment variable.

## Generate a news-style description

`generate_description.py` turns validated source text into a high-stakes,
news-style social description. It uses few-shot examples for paragraphing and
attribution, but the prompt instructs the model to use only the supplied source
text, lead with the most consequential angle, and not invent unsupported
catastrophe or complete truncated clauses.

```powershell
python .\generate_description.py `
  --tweet-json .\output\polymarket-tweet.json `
  --output .\output\polymarket-description.txt
```

## Editorial approval workflow

1. Send the assistant an X/Twitter status URL.
2. Fetch and validate the post through the free open-source FxTwitter backend.
3. The assistant writes a factual hook headline from the extracted full post.
4. Run `generate_post.py` with that headline and create a draft image.
5. Generate a description with `generate_description.py`, send it and the image
   to Telegram, then show the same package for review. Revise it until the user
   says `yes`.
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
  --description-file .\output\draft-description.txt `
  --stage preview `
  --send
```

Every materially revised image or description must be resent with
`--stage preview --send` before requesting approval again. The script sends the
image first and the full description as one or more separate Telegram messages.

## Validate or publish to Facebook

`publish_facebook.py` first confirms that the configured Page token belongs to
the expected Page. Without `--publish`, it performs validation only:

```powershell
python .\publish_facebook.py `
  --image .\output\approved-post.png `
  --message-file .\output\approved-description.txt
```

After the user has approved the exact latest preview image and description with `yes`,
publishing requires both safety arguments:

```powershell
python .\publish_facebook.py `
  --image .\output\approved-post.png `
  --message-file .\output\approved-description.txt `
  --publish `
  --confirm yes
```

Facebook credentials are loaded from `.env` and are never supplied on the
command line.

## Validate or publish to Instagram

Instagram Login publishing uses the configured `@bits_t0day` Business account.
Meta requires a public HTTPS image URL, so the approved workflow publishes the
image to Facebook first and reuses its Facebook-hosted image URL for Instagram.

Without `--publish`, this validates the account and publishing quota only:

```powershell
python .\publish_instagram.py `
  --image-url "https://public.example/approved-post.png" `
  --caption-file .\output\approved-description.txt
```

After explicit approval of the exact latest preview, publishing additionally requires:

```powershell
python .\publish_instagram.py `
  --image-url "https://public.example/approved-post.png" `
  --caption-file .\output\approved-description.txt `
  --publish `
  --confirm yes
```
