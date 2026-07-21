# Bits Today social post workflow

Use this workflow whenever the user supplies an X/Twitter status URL for a
social post.

1. Extract the post through `fetch_tweets.py`. It uses the free, MIT-licensed
   FxEmbed/FxTwitter backend and does not use X's official API, an X developer
   key, or Apify. Validate the returned tweet ID and non-empty text before using
   it. If the public endpoint is unavailable, report the exact error; a
   self-hosted FxEmbed deployment may be supplied with `--api-base`.
2. Write the headline in the Codex task from the extracted post. Preserve the
   central actor, action, scale, and important numbers. Do not call a separate
   text model from the Python generator and do not add unverified facts.
3. Generate a text-free editorial background and run `generate_post.py` with
   the chosen `--headline`. Pillow owns all gradient and typography rendering.
   Do not add a bottom footer or badge.
4. Render and inspect the draft, write a factual draft description, and send
   both through `notify_telegram.py --stage preview --send`. Only after that
   succeeds, show the same draft in the Codex task and ask for revisions or the
   exact approval word `proceed`. Reuse `--background-input` for typography or
   layout revisions that do not require a new background. Send every materially
   revised image-and-description pair to Telegram before asking again.
5. `proceed` means: prepare a detailed cross-platform description, send the
   final image plus description through `notify_telegram.py --stage final
   --send`, and return the same package in the Codex task for one last review.
   It does not authorize publishing.
6. Only a subsequent explicit `yes` referring to that exact final review
   package authorizes publishing to both the configured Facebook Page and
   Instagram account. Do not publish on ambiguous approval. If the package was
   changed after Telegram delivery, resend it before accepting final approval.
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
