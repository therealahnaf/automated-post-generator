# Bits Today news workflow

Use this workflow only after the `AGENTS.md` router has persisted
`workflow_type: news` in the fetched tweet JSON. Reuse that JSON and its
downloaded media; do not fetch or classify the story again.

1. Confirm the fetched JSON contains the requested tweet ID and non-empty text.
   Preserve the complete same-author thread, nested quoted-post text, and photo
   source order. Do not download videos or extract video frames. The generated
   primary plus secondary images must never exceed 10 images. Retain the
   router's persisted Facebook and Instagram language choices and
   `headline_highlight` choice (`cyan`, `red`, or `dual`). A Telegram-selected
   platform language is authoritative; never reroll either choice during the
   same story.
2. Write the English headline directly in the Codex task from the validated
   source. Make it immediately understandable to both technical and general
   readers: translate jargon into the concrete thing that happened and why a
   person should care. Use a bold, dramatic, consequence-first framing rather
   than a neutral summary. Prefer a specific actor plus a forceful verb and the
   largest defensible result, risk, contrast, scale, number, or power shift.
   Headlines such as `AI Solves a 100-Year-Old Math Problem`, `AI Just Did What
   Scientists Couldn't for Decades`, or `This AI Discovery Could Transform
   Cancer Research` illustrate the intended energy and clarity, but their facts
   are examples only.

   Headlines should be extremes: push the framing toward the strongest,
   highest-stakes, most surprising version that the validated facts can support.
   Additional examples of the intended style are:

   - `Kentucky School Teaches Students With Wrong AI-Generated Content`
   - `Claude Makes Progress On a 100 Year Old Math Problem`
   - `AI Agents Tried to Infiltrate an Open-Source Project`
   - `OpenAI CFO Leaves saying Mission Success Seems Near`

   For research and scientific stories, first state the concrete breakthrough
   in plain language. When the validated source or research establishes a
   credible downstream implication, connect it to the most consequential
   understandable outcome with words such as `could`, `may`, or `could lead
   to`. Make the significance feel extreme without making the underlying fact
   more certain or complete than it is. Never invent a link to curing cancer,
   solving a field, saving lives, or another high-stakes outcome merely to make
   the headline stronger. A partial result, simulation, preprint, benchmark, or
   early laboratory finding must not become a solved problem, proven treatment,
   or deployed breakthrough. Preserve important names and numbers. Avoid vague
   hype such as `game-changing`, `revolutionary`, or `shocking` when a concrete
   result can carry the headline. Do not use a separate text-model call for the
   original headline and do not add unverified facts.
   For each platform selecting Bangla, `tools/news/generate_post.py` must make one
   additional fixed `gpt-5.6-luna` call to translate the approved English
   headline into concise, natural Bangla that preserves the same plain-language
   clarity, dramatic force, and uncertainty. Reuse that translation when both
   platforms select Bangla. For English, render the approved English headline
   directly.
3. Run `tools/news/generate_post.py --headline`. When the first downloaded
   photo is eligible for the primary inset, do not call the image model: select
   a stable random `bg-*.png` from `assets/fonts/images` and reuse it across
   platform-language variants and revisions. When there is no downloaded photo,
   or the first photo is ineligible for the primary inset, generate one
   text-free editorial background with the image model. Pillow owns all gradient
   and typography rendering. Use `--style brand-block` with
   `#FF5757` and `#C2FFE1`. Render `Bits Today | <date>` and the transparent
   bottom-right logo. Use bundled Roboto for English headlines and the byline;
   retain the Bengali-capable Nirmala UI/Noto Sans Bengali path for Bangla.
4. Apply the persisted highlight treatment: `cyan` highlights only the first
   line in mint, `red` highlights only the first line in coral, and `dual`
   highlights the first line in coral and the second in mint. Do not add
   `Desk`, an AI-generated credit, an extra badge, or an experimental preset.
   Always retain the approved shared Codeastrix sponsor footer.
5. The generated post is always primary. If the tweet has photos, place the
   first downloaded photo uncropped in a borderless rounded-corner inset over
   the lower portion of the selected local background only when its
   orientation-independent source dimensions are at least 640x480 pixels and
   its fitted inset retains a useful minimum footprint. Never upscale it. A
   smaller or extreme-aspect-ratio photo remains secondary only, while the
   primary uses the generated editorial background. Keep an inset photo out of
   the secondary set so the carousel never repeats an image already visible in
   the generated primary.
6. Generate the bilingual description with
   `tools/news/generate_description.py`. The first fixed `gpt-5.6-luna` call
   creates consequential but source-grounded English copy; the second
   translates and summarizes it into concise Bangla. Include the complete
   thread and all nested quoted-post text. Preserve attribution and uncertainty
   and never invent catastrophe, certainty, or consequences. Preserve the
   generator's poster-identity policy through research revisions: include the
   original poster's `@username` only when they are unmistakably a major public
   figure or major official account; otherwise omit both their account name and
   handle. This does not suppress people explicitly named in the story text.
7. Search the internet for useful additional context. If useful details are
   found, enhance the English description and revise the Bangla
   translation-summary to match. If not, retain the generated copy. Never force
   irrelevant context into the post.
8. Run `tools/news/finalize_description.py`. End with:

    ```text
    Sources:
    <each research publisher actually used>
    ```

    Pass the supplied X URL and each research URL actually used to the
    finalizer for validation, but omit the X account attribution and never
    place a raw link in the caption. Use recognizable publisher/site labels for
    research sources, deduplicate repeated publishers, and keep the complete
    bilingual copy and source block within the configured platform limit. If no
    research source is used, omit the `Sources:` block entirely.
9. After research and source finalization, run
   `tools/news/generate_carousel_copy.py` with the fetched tweet JSON, the
   primary post's JSON sidecar, and the finalized bilingual description. It
   uses one fixed `gpt-5.6-luna` call to create concise, ordered,
   source-grounded story segments. Its prompt must include the approved English
   headline, complete tweet/thread and nested-quote text, and finalized English
   description. Every segment must add information beyond the headline and
   must not repeat or closely paraphrase the headline's claim. Exclude only the
   exact photo embedded in the primary, then create one segment for every
   remaining downloaded photo in source order. If the first photo was not
   embedded, retain it as the first detail card. Cap the secondary set at nine
   so the primary plus detail cards never exceed 10 items. If the tweet has no
   downloaded photos, create exactly one summary segment for a text-only second
   carousel item. If the tweet's only photo is already embedded in the primary,
   keep a one-item carousel and do not generate redundant detail copy.
10. If either platform selects Bangla and detail copy exists, run
    `tools/news/translate_carousel_copy.py` once and reuse that translated copy
    for every Bangla platform. Render each distinct platform package with
    `tools/news/generate_carousel.py --platform <platform>`, supplying its
    already-rendered primary image and the language-matched copy JSON. The
    primary remains the headline card. Every media detail card places its short
    description above the complete uncropped tweet image with rounded corners,
    a subtle shadow, and no border. Center the image vertically in the available
    media region below the description and above the footer regardless of its
    fitted height, over a stably selected local
    `assets/fonts/images/bg-*.png` background. The no-media summary card
    centers its segment over a local background. Reuse the same background seed
    across platform-language variants and revisions. Every card retains the
    approved shared Codeastrix footer.
11. Run `tools/news/prepare_platform_descriptions.py` after source finalization.
    English-selected platforms receive English first; Bangla-selected platforms
    receive Bangla first, then `---`, then the other language. Do not configure
    text models through `.env` or command-line flags. Render and inspect each
    distinct platform package, then follow the shared Telegram preview,
    revision, exact `yes` approval, Facebook, and Instagram contract in
    `AGENTS.md`. Pass `--platform facebook` or `--platform instagram` to both
    renderers as applicable; reuse `--background-input` for the primary and the
    persisted carousel background seed between platform variants and revisions.

Never store tokens in source, output metadata, shell scripts, or command
arguments.
