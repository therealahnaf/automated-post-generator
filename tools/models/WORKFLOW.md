# The Bits Today model-announcement workflow

Use this workflow for posts announcing or introducing an AI model.

1. Reuse the fetched tweet JSON created by the `AGENTS.md` workflow router and
   require its persisted `workflow_type` to be `model`. Do not fetch or
   reclassify the story again. Use English for both platforms.
   Photos are downloaded by the router; videos and video frames are ignored.
2. Generate the English long-form caption through
   `tools/models/generate_description.py`. Research the announcement on the
   internet, enhance the caption only with useful sourced details, and finalize it with
   `tools/news/finalize_description.py`. Under `Sources:`, show only
   recognizable labels for research publishers actually used. Omit the
   supplied X account attribution and do not place raw links in the caption.
   Preserve the
   shared generator's poster-identity policy: identify the original poster only
   when they are unmistakably a major public figure or major official account;
   otherwise omit their account name and `@username`.
3. Identify the exact announced model name and the releasing company or
   organization from the validated source. Never infer a company that is not
   supported by the announcement or its official source. Run
   `tools/models/generate_copy.py` with `--model-name`, `--company-name`, the
   tweet JSON, and the finalized description. The headline remains exactly
   `Meet <model name>`, and the copy JSON must persist `company_name`.
   The script uses the fixed `gpt-5.6-luna` model to split the finalized English
   description into ordered, source-grounded carousel segments. With photos,
   the segment count must exactly match the downloaded-photo count. Without
   photos, generate two or three segments according to the amount of detail.
4. Run `tools/models/generate_post.py`; never call an image model for this
   workflow. It renders the default Giant Mark design deterministically: every
   backdrop, highlight colour and marker shape is seeded from the validated
   post, so a re-render and both platforms are identical. Pass `--seed
   <integer>` only when deliberately overriding that sequence, and reuse the
   same seed for every revision and platform. Do not pass `--primary-style`;
   the classic styles remain only as an automatic fallback.
5. The first card is the Giant Mark cover: a darkened news backdrop, the
   releasing company's logo from `assets/models/logos/` large behind a big
   left-aligned `Meet <model name>` headline on the hand-cut marker, then
   `by <company name>` and the date. The logo is matched only from the
   company name; a company without a logo on file shows two or three large
   letters instead. Each downloaded photo follows in downloaded-photo order
   with its own short description. A landscape photo becomes a two-slide
   spread showing the complete, uncropped image across both slides, alternating
   Lead + Wide and Adaptive Max layouts; a portrait photo, or any photo beyond
   what fits, uses a single card. The renderer keeps the complete carousel
   within 10 images and alternates the coral and mint highlight per section.
   Preview and publish every slide it lists, in order; never drop one half of
   a spread. The website archive automatically receives each spread whole.
6. When no photos were downloaded, create one summary card for each of the two
   or three description segments, as the renderer does by default; do not
   generate any background.
7. Render one English package with
   `tools/models/generate_post.py --platform facebook` and reuse it for both
   platforms. Do not translate the headline or secondary copy. Run
   `tools/news/prepare_platform_descriptions.py --english-title "Meet <model name>"`
   with the finalized description to write both English captions and website
   manifests. Preserve model and company names. Then
   follow the shared Telegram preview, revision, exact `yes` approval,
   Facebook, and Instagram contract in `AGENTS.md`.

Never store tokens in source, output metadata, or command arguments. Read them
from the repository-root `.env` or an authenticated session.
