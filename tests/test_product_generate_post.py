import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from PIL import Image

from tools.products import generate_post


class ProductGeneratePostTests(unittest.TestCase):
    def setUp(self) -> None:
        background = Image.new("RGB", (1024, 1280), (18, 36, 54))
        payload = io.BytesIO()
        background.save(payload, format="PNG")
        self.background_bytes = payload.getvalue()

    def test_primary_renders_product_knowledge_stack(self) -> None:
        result = generate_post.compose_primary(
            self.background_bytes,
            "Claude Code",
            "Anthropic",
            "An AI agent that works inside your terminal",
            date(2026, 7, 24),
        )
        self.assertEqual(result.size, (1080, 1350))
        center = result.crop((40, 300, 1040, 1030))
        colors = center.getcolors(maxcolors=center.width * center.height) or []
        values = {color for _, color in colors}
        self.assertIn(generate_post.news_post.BRAND_CORAL[:3], values)
        self.assertIn(generate_post.news_post.BRAND_MINT[:3], values)
        footer = result.crop((0, 1192, 1080, 1350))
        footer_colors = footer.getcolors(
            maxcolors=footer.width * footer.height
        ) or []
        footer_values = {color for _, color in footer_colors}
        self.assertIn(
            generate_post.model_post.codeastrix_footer.BLUE_LIGHT[:3],
            footer_values,
        )
        self.assertIn(
            generate_post.model_post.codeastrix_footer.WHITE[:3],
            footer_values,
        )

    def test_primary_renders_bangla_platform_labels_and_intro(self) -> None:
        result = generate_post.compose_primary(
            self.background_bytes,
            "Claude Code",
            "Anthropic",
            "টার্মিনালের ভেতরে কাজ করা একটি AI এজেন্ট",
            date(2026, 7, 24),
            language="bangla",
        )
        self.assertEqual(result.size, (1080, 1350))

    def test_no_media_cli_reuses_model_secondary_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            background_dir = root / "backgrounds"
            background_dir.mkdir()
            for index, color in enumerate(
                ((18, 36, 54), (54, 18, 36), (18, 54, 36)),
                start=1,
            ):
                Image.new("RGB", (1024, 1280), color).save(
                    background_dir / f"bg-{index}.png"
                )
            tweet_path = root / "tweet.json"
            tweet_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "1",
                                "text": "A named product release without photos.",
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
                        "product_name": "Test Product",
                        "company_name": "Test Company",
                        "headline": "You Should Know About Test Product",
                        "intro_headline": "A tool that simplifies technical work",
                        "short_descriptions": [
                            "The first product feature.",
                            "The second product feature.",
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
                    "--background-dir",
                    str(background_dir),
                    "--seed",
                    "42",
                    "--output-dir",
                    str(output_dir),
                    "--date",
                    "2026-07-24",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / "01-product-test-product.png").is_file())
            self.assertTrue((output_dir / "02-summary-1.png").is_file())
            self.assertTrue((output_dir / "03-summary-2.png").is_file())
            metadata = json.loads(
                (output_dir / "post.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["primary_style"], "product-knowledge-stack")
            self.assertEqual(metadata["background_seed"], 42)
            self.assertEqual(len(metadata["background_sources"]), 3)


if __name__ == "__main__":
    unittest.main()
