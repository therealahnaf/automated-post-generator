# The Bits Today advertisement

`assets/banner-follow-the-journey.png` is the approved, tightly cropped
2172 × 233px artwork. It contains the logo, headline, website URL, and the line
“Read the latest news. Follow the journey.” Keep this asset in Git so local
and production renders use the same artwork. No network or image-model call is
needed to render the advertisement.

`footer.py` scales the asset proportionally (116px tall at 1080px wide).
Image workflows reserve space at the bottom. Reels place it immediately below
the fitted footage and reserve room for tall videos without cropping them.
The bar is composited after the outro layers so it remains visible throughout.

The historical `tools/news/codeastrix_footer.py` entry point remains a
compatibility adapter; workflow instruction files have not been changed.
