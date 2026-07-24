# Bits Today product-release workflow

Use this workflow only after the `AGENTS.md` router has persisted
`workflow_type: product` in the fetched tweet JSON. Reuse that JSON and its
   downloaded media; do not fetch or classify the story again.

1. Confirm the fetched JSON contains the requested tweet ID and non-empty text.
   Preserve the complete same-author thread, nested quoted-post text, ordered
   photos, persisted platform languages, and highlight choices. Both
   Telegram-selected platform languages are authoritative and must not be
   rerolled.
2. Generate the bilingual long-form caption through
   `tools/products/generate_description.py`. Research the announcement on the
   internet, enhance the caption only with useful sourced details, keep both
   languages synchronized, and finalize it with
   `tools/news/finalize_description.py`. The supplied X URL must appear first
   under `Sources:`, followed by research URLs actually used. Preserve the
   shared generator's poster-identity policy: identify the original poster only
   when they are unmistakably a major public figure or major official account;
   otherwise omit their account name and `@username`.
3. Identify the exact product name and releasing company or organization from
   the validated announcement. Do not classify an AI/ML model as a product and
   never invent a company or product name. Run
   `tools/products/generate_copy.py` with `--product-name`, `--company-name`,
   the tweet JSON, and finalized description.
4. The fixed primary headline is `You Should Know About <product name>`.
   `generate_copy.py` makes one fixed `gpt-5.6-luna` call that also creates:

   - a concrete 5–12 word `intro_headline` explaining what the product does or
     the main outcome it enables, without repeating the product/company name;
   - ordered, source-grounded carousel segments.

   With photos, the segment count must exactly match the downloaded-photo
   count. Without photos, create two or three segments according to the amount
   of distinct detail.
5. Generate one text-free product-launch background, then run
   `tools/products/generate_post.py`. The primary card uses the fixed
   `product-knowledge-stack` layout:

   ```text
   You Should Know About
   <PRODUCT NAME>
   <functional intro headline>
   by <company name>
   ```

   Keep this centered hierarchy and the coral/mint Bits Today palette.
6. For every downloaded photo, create a secondary card with its short
   description at the top and the complete, uncropped photo aligned toward the
   bottom. Preserve source order. The complete carousel must never exceed 10
   images.
7. With no photos, create one secondary summary card for each of the two or
   three description segments. Reuse the exact primary background; do not
   generate additional backgrounds.
8. If either platform selects Bangla, run
   `tools/news/translate_carousel_copy.py` once and reuse that translated copy
   JSON for every Bangla platform. Render each distinct package with
   `tools/products/generate_post.py --platform <platform>`; the renderer
   translates `You Should Know About` and `by`, while the translated copy
   supplies the functional intro and every text-bearing secondary card.
   Preserve product and company names and reuse the same background and source
   images across variants. Then follow the shared Telegram preview, revision,
   exact `yes` approval, Facebook, and Instagram contract in `AGENTS.md`.

Never store tokens in source, output metadata, or command arguments.
