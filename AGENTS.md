# Bits Today social post workflow

Use this workflow whenever the user supplies an X/Twitter status URL for a
social post.

1. Extract the full post through `fetch_tweets.py`. It uses the free,
   MIT-licensed FxEmbed/FxTwitter backend first and may recover longer text
   through the configured VxTwitter-compatible fallback when FxTwitter returns a
   possible long-post preview. It does not use X's official API, an X developer
   key, or Apify. Validate the returned tweet ID and non-empty text before using
   it. If full text cannot be recovered for a visibly truncated source, report
   the exact error instead of drafting from partial text. A self-hosted
   FxEmbed-compatible deployment may be supplied with `--api-base`.
2. Write the headline in the Codex task from the extracted full post. Treat the
   headline as the hook: lead with the most important, eye-catching actor,
   action, risk, contrast, scale, number, or power shift in the source. Preserve
   important names and numbers. Do not call a separate text model to write the
   headline and do not add unverified facts.
3. Generate a text-free editorial background and run `generate_post.py` with
   the chosen `--headline`. Pillow owns all gradient and typography rendering.
   The image byline must render only `Bits Today`; do not add `Desk`, an
   AI-generated credit line, a bottom footer, or a badge.
4. Render and inspect the draft, then generate a high-stakes news-style
   description with `generate_description.py`. Use the fetched source text or
   `--tweet-json` output, preserve attribution and uncertainty, and do not add
   facts from outside the validated source. Make the writing feel urgent and
   consequential, but do not invent catastrophe, certainty, or consequences
   beyond the source.
5. Send the draft image and generated description through
   `notify_telegram.py --stage preview --send`. Only after that succeeds, show
   the same draft in the Codex task and ask for revisions or the exact approval
   word `yes`. Reuse `--background-input` for typography or layout revisions
   that do not require a new background. Send every materially revised
   image-and-description pair to Telegram before asking again.
6. Only an explicit `yes` referring to the exact latest preview package
   authorizes publishing to both the configured Facebook Page and Instagram
   account. Do not publish on ambiguous approval. If the package was changed
   after Telegram delivery, resend it before accepting approval.
7. Invoke `publish_facebook.py` first after approval. Its publishing path
   additionally requires both `--publish` and `--confirm yes`. Retrieve the
   resulting Facebook-hosted image URL, then invoke `publish_instagram.py` with
   the same approved description; it has the same dual confirmation guard.
8. After publishing, return both platform post IDs or URLs. If one platform
   succeeds and the other fails, report the partial result accurately and do
   not create a duplicate post.

Never store API tokens in source, output metadata, shell scripts, or command
arguments. Read them from environment variables or an authenticated browser
session.
