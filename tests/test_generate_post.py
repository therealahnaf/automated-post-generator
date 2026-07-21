import io
import unittest
from datetime import date

from PIL import Image, ImageDraw, ImageFont

import generate_post


class GeneratePostTests(unittest.TestCase):
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
