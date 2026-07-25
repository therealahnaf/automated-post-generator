import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from PIL import Image

from tools.thoughts import generate_post


PARAGRAPHS = [
    (
        "AI is making intelligence feel abundant. Answers now arrive in seconds, "
        "but deciding what deserves attention remains a harder and more human task."
    ),
    (
        "The old advantage was knowing how to make something. The emerging "
        "advantage is knowing what should be made, what should be ignored, and why."
    ),
    (
        "The future may not belong to whoever has the most intelligence on demand. "
        "It may belong to whoever can direct that intelligence and own the result."
    ),
]


class ThoughtGeneratePostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.backgrounds = self.root / "backgrounds"
        self.backgrounds.mkdir()
        for index, color in enumerate(
            ((20, 20, 20), (45, 20, 20), (20, 45, 35)),
            start=1,
        ):
            Image.new("RGB", (700, 700), color).save(
                self.backgrounds / f"bg-{index}.png"
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_seeded_backgrounds_avoid_immediate_repeats(self) -> None:
        backgrounds = generate_post.list_backgrounds(self.backgrounds)
        first = generate_post.select_backgrounds(backgrounds, 8, seed=42)
        second = generate_post.select_backgrounds(backgrounds, 8, seed=42)
        self.assertEqual(first, second)
        self.assertTrue(
            all(left != right for left, right in zip(first, first[1:]))
        )

    def test_cover_and_paragraph_card_render_at_post_size(self) -> None:
        background = self.backgrounds / "bg-1.png"
        cover = generate_post.compose_cover(
            background,
            "Intelligence Is Cheap. Judgment Is Not.",
            date(2026, 7, 26),
            total_cards=4,
        )
        card = generate_post.compose_thought_card(
            background,
            PARAGRAPHS[0],
            date(2026, 7, 26),
            position=1,
            paragraph_count=3,
        )
        self.assertEqual(cover.size, (1080, 1350))
        self.assertEqual(card.size, (1080, 1350))
        self.assertNotEqual(cover.getbbox(), None)
        self.assertNotEqual(card.getbbox(), None)

    def test_cli_outputs_ordered_carousel_metadata_and_preview(self) -> None:
        copy_path = self.root / "copy.json"
        copy_path.write_text(
            json.dumps(
                {
                    "series_title": "Today's Tokens for Thought",
                    "hook": "Intelligence Is Cheap. Judgment Is Not.",
                    "paragraphs": PARAGRAPHS,
                }
            ),
            encoding="utf-8",
        )
        output = self.root / "cards"

        exit_code = generate_post.main(
            [
                "--copy-json",
                str(copy_path),
                "--background-dir",
                str(self.backgrounds),
                "--output-dir",
                str(output),
                "--seed",
                "42",
                "--date",
                "2026-07-26",
            ]
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue((output / "01-cover.png").is_file())
        self.assertTrue((output / "04-thought-3.png").is_file())
        self.assertTrue((output / "preview-contact-sheet.png").is_file())
        metadata = json.loads((output / "post.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["random_seed"], 42)
        self.assertEqual(len(metadata["images"]), 4)
        self.assertEqual(len(metadata["background_sources"]), 4)


if __name__ == "__main__":
    unittest.main()
