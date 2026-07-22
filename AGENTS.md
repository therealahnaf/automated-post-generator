# Bits Today social post workflow

Use this workflow whenever the user supplies an X/Twitter status URL for a
social post.

Do not stop partway through this workflow: continue until the full workflow is
complete or until an explicit user approval is required.

1. Extract the full post and its same-author thread through `fetch_tweets.py`.
   It uses FxTwitter v2's `/thread/{id}` endpoint from the free, MIT-licensed
   FxEmbed project. It does not use X's official API, an X developer key, or
   Apify. Validate the returned tweet ID and non-empty text before using it. A
   self-hosted FxEmbed-compatible deployment may be supplied with `--api-base`.
   Always use `--media-dir` to download photos from the thread and nested quoted
   posts, and to extract a JPEG opening frame from every attached video with
   FFmpeg. Preserve thread, quote, and media source order. The generated main
   post plus these secondary images must never exceed 10 images total. Its
   default `--language auto` must randomly select `english` or
   `bangla` once and persist the result as `post_language` in the tweet JSON.
   Its default `--highlight-style auto` must also randomly select `cyan`, `red`,
   or `dual` once and persist it as `headline_highlight`. Never reroll either
   choice later in the same story workflow.
2. Write the English headline in the Codex task from the extracted full post. Treat the
   headline as the hook: lead with the most important, eye-catching actor,
   action, risk, contrast, scale, number, or power shift in the source. Preserve
   important names and numbers. Do not call a separate text model to write the
   original English headline and do not add unverified facts. If the persisted
   language is `bangla`, pass the tweet JSON to `generate_post.py`; it must use
   one additional `gpt-5.6-luna` call to translate the approved English headline
   into concise Bangla and render that translation. If the language is
   `english`, render the approved English headline without a translation call.
3. Generate a text-free editorial background and run `generate_post.py` with
   the chosen `--headline`. Pillow owns all gradient and typography rendering.
   Always use the approved `--style brand-block` Pillow preset with the brand
   colors `#FF5757` and `#C2FFE1`. The byline must render `Bits Today | <date>`,
   and the transparent Bits Today logo must appear in the bottom-right corner.
   English headlines and the byline must use the bundled Roboto family. Bangla
   headlines must retain the Bengali-capable Nirmala UI/Noto Sans Bengali path.
   Apply the persisted highlight treatment: `cyan` highlights only the first
   line in mint, `red` highlights only the first line in coral, and `dual`
   highlights the first line in coral and the second in mint.
   Do not add `Desk`, an AI-generated credit line, a bottom footer, or an extra
   badge. Keep the other presets for explicit style experiments only; do not
   select them during the normal publishing workflow. This generated post is
   always the primary image. Run every downloaded tweet photo and extracted
   video opening frame through
   `brand_tweet_images.py` so the complete source is contained without cropping
   inside a 1080x1350 `#212121` frame. The frame must fill any unused 4:5 canvas
   area. Keep the bottom-right transparent Bits Today logo overlay with no
   background plate. The branded
   media images follow the generated post as secondary images in their original
   order, up to 10 images total.
4. Render and inspect the draft, then generate a bilingual description with
   `generate_description.py`. Its first model call creates the high-stakes
   English news description; its second model call translates and summarizes
   that English copy into concise Bangla. Both text-generation calls must use
   the fixed `gpt-5.6-luna` model; do not configure text models through `.env`
   or command-line flags. The saved result must follow the persisted language
   choice: English first for `english`, or Bangla first for `bangla`, then the
   separator `---`, then the other language.
   When using `--tweet-json`, the description source must include the complete
   fetched same-author thread and all nested quoted-post text. Preserve
   attribution and uncertainty in both languages, and do not add facts from
   outside the validated source. Make the writing feel urgent and consequential, but do not
   invent catastrophe, certainty, or consequences beyond the source.
5. After description generation, search the internet for additional relevant
   details about the story. If the search produces useful information, enhance
   the English description with that context and revise the Bangla
   translation-summary so it matches the final English version while retaining
   the selected language order. If no useful
   additional details are found, keep the generated bilingual description as
   it is. Do not force extra context into the post. Keep the final bilingual
   copy within the configured platform length limit and retain the `---`
   separator.
6. Send the generated main image, every ordered secondary media image, and the
   enhanced bilingual description through `notify_telegram.py --stage preview
   --send`, using one `--secondary-image` argument per media image. Only after
   that succeeds, show the same complete image set in the Codex task and ask for
   revisions or the exact approval word `yes`. Reuse `--background-input` for
   typography or layout revisions that do not require a new background. Send
   every materially revised image-and-description package to Telegram before
   asking again.
7. Only an explicit `yes` referring to the exact latest preview package
   authorizes publishing to both the configured Facebook Page and Instagram
   account. Do not publish on ambiguous approval. If the package was changed
   after Telegram delivery, resend it before accepting approval.
8. Invoke `publish_facebook.py` first after approval, passing the generated post
   through `--image` and each media image through `--secondary-image` in source
   order. Its publishing path additionally requires both `--publish` and
   `--confirm yes`. For multiple images, it must create one ordered Facebook
   multi-photo post and return every Facebook-hosted image URL. Invoke
   `publish_instagram.py` with the first hosted URL as `--image-url` and the
   remaining URLs as ordered `--secondary-image-url` arguments; it must publish
   one Instagram carousel with the same approved description and image order.
   With no secondary media, both publishers retain their single-image behavior.
9. After publishing, return both platform post IDs or URLs. If one platform
   succeeds and the other fails, report the partial result accurately and do
   not create a duplicate post.

For an unattended Telegram queue task only, the exact final instruction
`NO NEED TO SEND PREVIEW. AUTOMATICALLY POST THE GENERATED POST` is explicit
authorization to skip steps 6 and 7 and publish the completed package without
asking for approval. This exception does not apply to interactive tasks or to
similar, paraphrased instructions. The full workflow must otherwise run to
completion, including source validation, rendering, description generation,
publishing to both platforms, and accurate reporting of partial failures.

Never store API tokens in source, output metadata, shell scripts, or command
arguments. Read them from environment variables or an authenticated browser
session.
