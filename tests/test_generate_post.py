import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont

import generate_post


class GeneratePostTests(unittest.TestCase):
    def test_english_brand_font_is_roboto(self) -> None:
        font = generate_post.load_roboto_font(size=48, bold=True)
        self.assertEqual(font.getname()[0], "Roboto")

    def test_headline_highlight_variants(self) -> None:
        self.assertEqual(
            generate_post.headline_highlight_colors(0, "cyan"),
            (generate_post.BRAND_MINT, generate_post.INK),
        )
        self.assertIsNone(generate_post.headline_highlight_colors(1, "cyan"))
        self.assertEqual(
            generate_post.headline_highlight_colors(0, "red"),
            (generate_post.BRAND_CORAL, generate_post.WHITE),
        )
        self.assertIsNone(generate_post.headline_highlight_colors(1, "red"))
        self.assertEqual(
            generate_post.headline_highlight_colors(0, "dual"),
            (generate_post.BRAND_CORAL, generate_post.WHITE),
        )
        self.assertEqual(
            generate_post.headline_highlight_colors(1, "dual"),
            (generate_post.BRAND_MINT, generate_post.INK),
        )

    def test_translates_headline_with_fixed_luna_model(self) -> None:
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                output_text="দেশীয় চিপে ১ গিগাওয়াট ডেটা সেন্টার চালু",
                output=[],
            )

        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        translated = generate_post.translate_headline_to_bangla(
            client,
            "Domestic chips power a one-gigawatt data center",
        )

        self.assertTrue(generate_post.contains_bangla_text(translated))
        self.assertEqual(calls[0]["model"], "gpt-5.6-luna")
        self.assertEqual(calls[0]["reasoning"], {"effort": "none"})

    def test_brand_block_renders_bangla_headline(self) -> None:
        background = Image.new("RGB", (1024, 1280), (35, 70, 100))
        payload = io.BytesIO()
        background.save(payload, format="PNG")

        result = generate_post.compose_post(
            payload.getvalue(),
            "দেশীয় চিপে ১ গিগাওয়াট ডেটা সেন্টার চালু",
            source="Bits Today",
            post_date=date(2026, 7, 22),
            credit="",
            style="brand-block",
        )

        self.assertEqual(result.size, (1080, 1350))

    def test_normalize_news_text_removes_invisible_characters(self) -> None:
        raw = "NEW: China’s Z.\u200bAI  begins\noperating"
        self.assertEqual(normalize := generate_post.normalize_news_text(raw), "NEW: China’s Z.AI begins operating")
        self.assertNotIn("\u200b", normalize)

    def test_wrap_headline_respects_width(self) -> None:
        image = Image.new("RGB", (600, 600), "black")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(generate_post.find_font(True), 48)
        lines = generate_post.wrap_headline(
            draw,
            "China’s Z.AI opens gigawatt-scale domestic-chip data center",
            font,
            480,
        )
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(generate_post.text_width(draw, line, font), 480)

    def test_build_byline_renders_brand_only(self) -> None:
        self.assertEqual(generate_post.build_byline("Bits Today Desk"), "Bits Today")
        self.assertEqual(generate_post.build_byline("  Bits Today  "), "Bits Today")
        self.assertEqual(generate_post.build_byline(""), "Bits Today")

    def test_build_byline_text_places_date_beside_brand(self) -> None:
        self.assertEqual(
            generate_post.build_byline_text(
                "Bits Today Desk", date(2026, 7, 21)
            ),
            "Bits Today | 21 Jul 2026",
        )

    def test_image_prompt_is_story_specific_not_hardcoded(self) -> None:
        prompt = generate_post.build_image_prompt(
            "Anthropic reached a $1.5 billion copyright settlement.",
            "Anthropic Reaches $1.5B AI Copyright Settlement",
        )

        self.assertIn("Anthropic reached", prompt)
        self.assertIn("courtrooms", prompt)
        self.assertNotIn("data-center campus in China", prompt)

    def test_compose_post_outputs_expected_canvas_and_red_highlight(self) -> None:
        background = Image.new("RGB", (1024, 1280), (35, 70, 100))
        payload = io.BytesIO()
        background.save(payload, format="PNG")
        result = generate_post.compose_post(
            payload.getvalue(),
            "China’s Z.AI opens gigawatt-scale domestic-chip data center",
            source="Bits Today",
            post_date=date(2026, 7, 21),
            credit="",
        )
        self.assertEqual(result.size, (1080, 1350))
        sample = result.crop((30, 30, 1050, 500))
        pixels = (
            sample.get_flattened_data()
            if hasattr(sample, "get_flattened_data")
            else sample.getdata()
        )
        red_pixels = sum(
            1
            for red, green, blue in pixels
            if red > 240 and 70 < green < 110 and blue < 110
        )
        self.assertGreater(red_pixels, 1000)

        footer = result.crop((0, 1200, 1080, 1350))
        footer_red_pixels = sum(
            1
            for red, green, blue in (
                footer.get_flattened_data()
                if hasattr(footer, "get_flattened_data")
                else footer.getdata()
            )
            if red > 200 and green < 50 and blue < 60
        )
        self.assertEqual(footer_red_pixels, 0)

    def test_first_tweet_photo_is_discovered_and_rounded_without_cropping(self) -> None:
        background = Image.new("RGB", (1024, 1280), (35, 70, 100))
        payload = io.BytesIO()
        background.save(payload, format="PNG")

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            media = root / "media"
            media.mkdir()
            photo_path = media / "first-photo.jpg"
            photo = Image.new("RGB", (900, 1200), (250, 250, 250))
            ImageDraw.Draw(photo).rectangle((0, 0, 899, 80), fill=(10, 200, 30))
            photo.save(photo_path, quality=100, subsampling=0)
            tweet_json = root / "tweet.json"
            tweet_json.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "downloaded_photos": [
                                    {"local_path": str(photo_path)}
                                ]
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            discovered = generate_post.find_first_tweet_photo(tweet_json)
            self.assertEqual(discovered, photo_path)
            result = generate_post.compose_post(
                payload.getvalue(),
                "A short headline",
                source="Bits Today",
                post_date=date(2026, 7, 23),
                credit="",
                logo_path=None,
                feature_image_path=discovered,
            )

        self.assertEqual(result.size, (1080, 1350))
        # A large portrait source is reduced to 465x620 without cropping.
        red, green, _ = result.getpixel((540, 555))
        self.assertLess(red, 40)
        self.assertGreater(green, 170)
        # The photo's top-left is rounded instead of remaining a square corner.
        corner = result.getpixel((307, 550))
        self.assertFalse(corner[0] < 40 and corner[1] > 170)

    def test_small_feature_photo_is_ineligible_and_never_upscaled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            photo_path = Path(temporary_directory) / "small.png"
            Image.new("RGB", (120, 90), (210, 30, 40)).save(photo_path)

            self.assertFalse(
                generate_post.feature_photo_meets_minimum(photo_path)
            )
            canvas = Image.new("RGBA", (1080, 1350), (0, 0, 0, 255))
            generate_post.paste_feature_photo(canvas, photo_path)

        pixels = canvas.load()
        red_points = [
            (x, y)
            for y in range(canvas.height)
            for x in range(canvas.width)
            if pixels[x, y][0] > 180
            and pixels[x, y][1] < 60
            and pixels[x, y][2] < 70
        ]
        xs = [point[0] for point in red_points]
        ys = [point[1] for point in red_points]
        self.assertLessEqual(max(xs) - min(xs) + 1, 120)
        self.assertLessEqual(max(ys) - min(ys) + 1, 90)

    def test_all_brand_styles_include_palette_and_bottom_right_logo(self) -> None:
        background = Image.new("RGB", (1024, 1280), (35, 70, 100))
        payload = io.BytesIO()
        background.save(payload, format="PNG")

        for style in generate_post.STYLE_CHOICES:
            with self.subTest(style=style):
                result = generate_post.compose_post(
                    payload.getvalue(),
                    "China Z.AI opens gigawatt-scale domestic-chip data center",
                    source="Bits Today",
                    post_date=date(2026, 7, 21),
                    credit="",
                    style=style,
                )
                self.assertEqual(result.size, (1080, 1350))

                headline = result.crop((30, 30, 1050, 570))
                colors = headline.getcolors(
                    maxcolors=headline.width * headline.height
                ) or []
                values = {color for _, color in colors}
                self.assertIn(generate_post.BRAND_CORAL[:3], values)
                self.assertIn(generate_post.BRAND_MINT[:3], values)

                logo = result.crop((860, 1160, 1080, 1350))
                logo_colors = logo.getcolors(maxcolors=logo.width * logo.height) or []
                logo_values = {color for _, color in logo_colors}
                self.assertIn(generate_post.BRAND_CORAL[:3], logo_values)
                self.assertIn(generate_post.BRAND_MINT[:3], logo_values)


if __name__ == "__main__":
    unittest.main()
