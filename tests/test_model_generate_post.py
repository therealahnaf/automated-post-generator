import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from PIL import Image

from tools.models import generate_post


class ModelGeneratePostTests(unittest.TestCase):
    def setUp(self) -> None:
        background = Image.new("RGB", (1024, 1280), (18, 36, 54))
        payload = io.BytesIO()
        background.save(payload, format="PNG")
        self.background_bytes = payload.getvalue()

    def test_primary_renders_fixed_headline_in_middle(self) -> None:
        result = generate_post.compose_primary(
            self.background_bytes,
            "Gemini 3.6 Flash",
            date(2026, 7, 23),
        )

        self.assertEqual(result.size, (1080, 1350))
        center = result.crop((40, 470, 1040, 840))
        colors = center.getcolors(maxcolors=center.width * center.height) or []
        values = {color for _, color in colors}
        self.assertIn(generate_post.news_post.BRAND_CORAL[:3], values)
        self.assertIn(generate_post.news_post.BRAND_MINT[:3], values)

    def test_primary_style_options_render_in_center_region(self) -> None:
        for style in (
            "launch-label",
            "glass-frame",
            "signal-stack-condensed",
            "signal-stack-editorial",
            "signal-stack-industrial",
        ):
            with self.subTest(style=style):
                result = generate_post.compose_primary(
                    self.background_bytes,
                    "Gemini 3.6 Flash",
                    date(2026, 7, 23),
                    style=style,
                )
                center = result.crop((60, 390, 1020, 900))
                colors = center.getcolors(
                    maxcolors=center.width * center.height
                ) or []
                values = {color for _, color in colors}
                self.assertIn(generate_post.news_post.BRAND_CORAL[:3], values)
                self.assertIn(generate_post.news_post.BRAND_MINT[:3], values)

    def test_media_secondary_puts_description_above_lower_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "launch.png"
            Image.new("RGB", (900, 500), (20, 80, 220)).save(source_path)
            result = generate_post.compose_media_secondary(
                source_path,
                "Higher intelligence, greater token efficiency, and a lower price.",
                date(2026, 7, 23),
            )

        self.assertEqual(result.size, (1080, 1350))
        top = result.crop((40, 80, 1040, 500))
        lower = result.crop((40, 650, 1040, 1280))
        self.assertNotEqual(top.getbbox(), None)
        blue_pixels = sum(
            1
            for red, green, blue in lower.getdata()
            if blue > 180 and red < 60 and green < 120
        )
        self.assertGreater(blue_pixels, 100_000)

    def test_no_media_summary_reuses_primary_background(self) -> None:
        primary = generate_post.compose_primary(
            self.background_bytes,
            "Gemini 3.6 Flash",
            date(2026, 7, 23),
        )
        summary = generate_post.compose_fallback_secondary(
            self.background_bytes,
            "Built to be more useful in real-world scenarios.",
            date(2026, 7, 23),
        )

        self.assertEqual(primary.getpixel((20, 20)), summary.getpixel((20, 20)))
        self.assertNotEqual(primary.getpixel((540, 650)), summary.getpixel((540, 650)))

    def test_no_media_cli_renders_one_card_per_description_segment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            background_path = root / "background.png"
            background_path.write_bytes(self.background_bytes)
            tweet_path = root / "tweet.json"
            tweet_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "1",
                                "text": "A model launch without attached photos.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            copy_path = root / "copy.json"
            copy_path.write_text(
                json.dumps(
                    {
                        "model_name": "Test Model",
                        "headline": "Meet Test Model",
                        "short_descriptions": [
                            "The first description segment.",
                            "The second description segment.",
                            "The third description segment.",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output_dir = root / "cards"

            exit_code = generate_post.main(
                [
                    "--tweet-json",
                    str(tweet_path),
                    "--copy-json",
                    str(copy_path),
                    "--background-input",
                    str(background_path),
                    "--output-dir",
                    str(output_dir),
                    "--date",
                    "2026-07-23",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / "02-summary-1.png").is_file())
            self.assertTrue((output_dir / "03-summary-2.png").is_file())
            self.assertTrue((output_dir / "04-summary-3.png").is_file())


if __name__ == "__main__":
    unittest.main()
