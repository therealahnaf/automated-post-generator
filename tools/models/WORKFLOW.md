# Bits Today model-announcement workflow

Use this workflow for posts announcing or introducing an AI model.

1. Reuse the fetched tweet JSON created by the `AGENTS.md` workflow router and
   require its persisted `workflow_type` to be `model`. Do not fetch or
   reclassify the story again. Photos are downloaded by the router; videos and
   video frames are ignored.
2. Generate the bilingual long-form caption through
   `tools/models/generate_description.py`. Research the announcement on the
   internet, enhance the caption only with useful sourced details, keep both
   languages synchronized, and finalize it with
   `tools/news/finalize_description.py`. The supplied X URL must appear first
   under `Sources:`, followed by research URLs actually used.
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
4. Generate one text-free model-launch background, then run
   `tools/models/generate_post.py`. The first card places a larger `Meet`, the
   model name, and `by <company name>` directly beneath it in the middle using
   the default `signal-stack-condensed` preset: centered condensed model-name
   typography with no side rule. For each downloaded
   photo, create a secondary card with its short description at the top and the
   complete, uncropped photo aligned toward the bottom. Keep the generated
   primary first and preserve downloaded-photo order. The complete carousel
   must never exceed 10 images.
5. When no photos were downloaded, create one secondary summary card for each
   of the two or three description segments. Every summary card must reuse the
   exact generated primary background with its segment centered. Do not
   generate additional backgrounds.
6. Render and inspect the complete package, then follow the shared Telegram
   preview, revision, exact `yes` approval, Facebook, and Instagram contract in
   `AGENTS.md`. Send and publish the generated primary first, followed by every
   secondary card in order.

Never store tokens in source, output metadata, or command arguments. Read them
from the repository-root `.env` or an authenticated session.
