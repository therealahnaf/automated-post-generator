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

BANGLA_PARAGRAPHS = [
    "কৃত্রিম বুদ্ধিমত্তা কাজকে সহজলভ্য করলে মানুষের মূল্যবোধ নিয়ে নতুন প্রশ্ন তৈরি হয়। উত্তর খোঁজার দায়িত্ব তখনও মানুষেরই থাকে।",
    "প্রযুক্তি আমাদের সক্ষমতা বাড়াতে পারে, কিন্তু কোন লক্ষ্য গুরুত্বপূর্ণ তা নিজে ঠিক করতে পারে না। সেই বিচার মানুষের অভিজ্ঞতা থেকে আসে।",
    "ভবিষ্যৎ শুধু দ্রুততম ব্যবস্থার হাতে থাকবে না। যত্ন, দায়িত্ব এবং সঠিক দিক বেছে নেওয়ার ক্ষমতাই মানুষের ভূমিকা নির্ধারণ করবে।",
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
        self.assertEqual(metadata["post_language"], "english")
        self.assertIsNone(metadata["platform"])

    def test_platform_specific_copies_share_stable_background_sequence(self) -> None:
        tweet_path = self.root / "tweet.json"
        tweet_path.write_text(
            json.dumps(
                {
                    "post_language": "english",
                    "platform_languages": {
                        "facebook": "english",
                        "instagram": "bangla",
                    },
                    "items": [
                        {
                            "id": "1",
                            "text": (
                                "AI changes the work people do while leaving "
                                "human judgment and responsibility essential."
                            ),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        english_copy = self.root / "english.json"
        english_copy.write_text(
            json.dumps(
                {
                    "series_title": "Today's Tokens for Thought",
                    "hook": "What remains distinctly human as AI advances?",
                    "paragraphs": PARAGRAPHS,
                    "copy_language": "english",
                    "source_tweet_json": str(tweet_path),
                }
            ),
            encoding="utf-8",
        )
        bangla_copy = self.root / "bangla.json"
        bangla_copy.write_text(
            json.dumps(
                {
                    "series_title": "আজকের ভাবনার খোরাক",
                    "hook": "AI এগিয়ে গেলে মানুষের নিজস্ব ভূমিকা কী থাকে?",
                    "paragraphs": BANGLA_PARAGRAPHS,
                    "copy_language": "bangla",
                    "source_tweet_json": str(tweet_path),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        facebook_output = self.root / "facebook"
        instagram_output = self.root / "instagram"
        facebook_exit = generate_post.main(
            [
                "--tweet-json",
                str(tweet_path),
                "--platform",
                "facebook",
                "--copy-json",
                str(english_copy),
                "--background-dir",
                str(self.backgrounds),
                "--output-dir",
                str(facebook_output),
            ]
        )
        instagram_exit = generate_post.main(
            [
                "--tweet-json",
                str(tweet_path),
                "--platform",
                "instagram",
                "--copy-json",
                str(bangla_copy),
                "--background-dir",
                str(self.backgrounds),
                "--output-dir",
                str(instagram_output),
            ]
        )

        self.assertEqual(facebook_exit, 0)
        self.assertEqual(instagram_exit, 0)
        facebook_metadata = json.loads(
            (facebook_output / "post.json").read_text(encoding="utf-8")
        )
        instagram_metadata = json.loads(
            (instagram_output / "post.json").read_text(encoding="utf-8")
        )
        self.assertEqual(facebook_metadata["post_language"], "english")
        self.assertEqual(instagram_metadata["post_language"], "bangla")
        self.assertEqual(facebook_metadata["platform"], "facebook")
        self.assertEqual(instagram_metadata["platform"], "instagram")
        self.assertEqual(
            facebook_metadata["random_seed"],
            instagram_metadata["random_seed"],
        )
        self.assertEqual(
            facebook_metadata["background_sources"],
            instagram_metadata["background_sources"],
        )


if __name__ == "__main__":
    unittest.main()
