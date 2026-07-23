# Bits Today news workflow

Use this workflow only after the `AGENTS.md` router has persisted
`workflow_type: news` in the fetched tweet JSON. Reuse that JSON and its
downloaded media; do not fetch or classify the story again.

1. Confirm the fetched JSON contains the requested tweet ID and non-empty text.
   Preserve the complete same-author thread, nested quoted-post text, and photo
   source order. Do not download videos or extract video frames. The generated
   primary plus secondary images must never exceed 10 images. Retain the
   router's one-time random `post_language` choice (`english` or `bangla`) and
   `headline_highlight` choice (`cyan`, `red`, or `dual`); never reroll either
   choice during the same story.
2. Write the English headline directly in the Codex task from the validated
   source. Lead with the strongest actor, action, risk, contrast, scale, number,
   or power shift. Preserve important names and numbers. Do not use a separate
   text-model call for the original headline and do not add unverified facts.
   For a persisted Bangla post, `tools/news/generate_post.py` must make one
   additional fixed `gpt-5.6-luna` call to translate the approved English
   headline into concise Bangla. For English, render the approved English
   headline directly.
3. Generate one text-free editorial background and run
   `tools/news/generate_post.py --headline`. Pillow owns all gradient and
   typography rendering. Use `--style brand-block` with `#FF5757` and
   `#C2FFE1`. Render `Bits Today | <date>` and the transparent bottom-right
   logo. Use bundled Roboto for English headlines and the byline; retain the
   Bengali-capable Nirmala UI/Noto Sans Bengali path for Bangla.
4. Apply the persisted highlight treatment: `cyan` highlights only the first
   line in mint, `red` highlights only the first line in coral, and `dual`
   highlights the first line in coral and the second in mint. Do not add
   `Desk`, an AI-generated credit, a footer, an extra badge, or an experimental
   preset.
5. The generated post is always primary. If the tweet has photos, place the
   first downloaded photo uncropped in a rounded-corner frame over the lower
   portion of the same background only when it is at least 640x480 pixels.
   Never upscale it. A smaller photo remains secondary only. Keep an inset
   photo out of the secondary set so the carousel never repeats an image
   already visible in the generated primary.
6. Run every downloaded photo through
   `tools/news/brand_tweet_images.py --post-metadata <primary-post.json>`.
   The primary post's JSON sidecar records `feature_image_source`; the branding
   command must exclude that exact photo and retain all other photos in
   original source order. If no photo was embedded because the first photo was
   smaller than 640x480, exclude nothing. Contain every remaining source
   without cropping inside a 1080x1350 `#212121` frame that fills unused 4:5
   space. Add the transparent Bits Today logo at bottom-right without a
   background plate. Place these branded images after the generated primary,
   within the 10-image total.
7. Generate the bilingual description with
   `tools/news/generate_description.py`. The first fixed `gpt-5.6-luna` call
   creates consequential but source-grounded English copy; the second
   translates and summarizes it into concise Bangla. Include the complete
   thread and all nested quoted-post text. Preserve attribution and uncertainty
   and never invent catastrophe, certainty, or consequences.
8. Order the languages using the persisted choice: English first for
   `english`, Bangla first for `bangla`, then `---`, then the other language.
   Do not configure text models through `.env` or command-line flags.
9. Search the internet for useful additional context. If useful details are
   found, enhance the English description and revise the Bangla
   translation-summary to match. If not, retain the generated copy. Never force
   irrelevant context into the post.
10. Run `tools/news/finalize_description.py`. End with:

    ```text
    Sources:
    <supplied X URL>
    <each research URL actually used>
    ```

    Keep the X URL first, include only used research sources, deduplicate URLs,
    and keep the complete bilingual copy and source block within the configured
    platform limit.
11. Render and inspect the complete package, then follow the shared Telegram
    preview, revision, exact `yes` approval, Facebook, and Instagram contract in
    `AGENTS.md`. Reuse `--background-input` for typography or layout revisions
    that do not require a new background.

Never store tokens in source, output metadata, shell scripts, or command
arguments.
