import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.models import generate_copy


class ModelGenerateCopyTests(unittest.TestCase):
    def test_headline_is_always_meet_model_name(self) -> None:
        self.assertEqual(
            generate_copy.build_headline("Gemini 3.6 Flash"),
            "Meet Gemini 3.6 Flash",
        )

    def test_company_name_is_normalized_for_primary_credit(self) -> None:
        self.assertEqual(
            generate_copy.normalize_company_name("  Google DeepMind  "),
            "Google DeepMind",
        )

    def test_generates_exact_number_of_source_grounded_cards(self) -> None:
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                output_text=json.dumps(
                    [
                        "Higher intelligence and stronger token efficiency.",
                        "A lower price shaped by developer feedback.",
                    ]
                ),
                output=[],
            )

        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        result = generate_copy.generate_short_descriptions(
            client,
            "Gemini 3.6 Flash has higher intelligence, improved token "
            "efficiency, and a lower price based on developer feedback.",
            "Gemini 3.6 Flash",
            (2, 2),
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(calls[0]["model"], "gpt-5.6-luna")
        self.assertEqual(calls[0]["reasoning"], {"effort": "none"})

    def test_no_downloaded_photos_requires_two_or_three_summary_cards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tweet.json"
            path.write_text(
                json.dumps({"items": [{"id": "1", "text": "Model launch"}]}),
                encoding="utf-8",
            )
            photo_count = generate_copy.downloaded_photo_count(path)
            self.assertEqual(photo_count, 0)
            self.assertEqual(
                generate_copy.required_card_range(photo_count),
                (2, 3),
            )

    def test_media_card_count_matches_downloaded_photo_count(self) -> None:
        self.assertEqual(generate_copy.required_card_range(4), (4, 4))

    def test_reads_english_section_from_finalized_bilingual_description(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "description.txt"
            path.write_text(
                "English launch description.\n\n---\n\nবাংলা বিবরণ।\n\n"
                "Sources:\nhttps://x.com/example/status/1\n",
                encoding="utf-8",
            )
            self.assertEqual(
                generate_copy.read_english_description(path),
                "English launch description.",
            )


if __name__ == "__main__":
    unittest.main()
