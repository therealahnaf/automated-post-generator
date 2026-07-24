import json
import unittest
from types import SimpleNamespace

from tools.products import generate_copy


class ProductGenerateCopyTests(unittest.TestCase):
    def test_fixed_headline_uses_product_name(self) -> None:
        self.assertEqual(
            generate_copy.build_headline("Claude Code"),
            "You Should Know About Claude Code",
        )

    def test_parses_functional_intro_and_exact_card_count(self) -> None:
        payload = {
            "intro_headline": "An AI agent that works inside your terminal",
            "short_descriptions": [
                "Reads and edits files across a software project.",
                "Runs development tools with user-controlled permissions.",
            ],
        }
        intro, descriptions = generate_copy.parse_product_copy(
            json.dumps(payload),
            (2, 2),
        )
        self.assertEqual(intro, payload["intro_headline"])
        self.assertEqual(descriptions, payload["short_descriptions"])

    def test_generation_uses_fixed_text_model(self) -> None:
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                output_text=json.dumps(
                    {
                        "intro_headline": "A wearable that organizes conversations",
                        "short_descriptions": [
                            "Captures conversations for later recall.",
                            "Organizes recorded information into searchable context.",
                        ],
                    }
                ),
                output=[],
            )

        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        _, descriptions = generate_copy.generate_product_copy(
            client,
            "The product records and organizes conversations.",
            "Pendant",
            "Example Company",
            (2, 2),
        )
        self.assertEqual(len(descriptions), 2)
        self.assertEqual(calls[0]["model"], "gpt-5.6-luna")
        self.assertEqual(calls[0]["reasoning"], {"effort": "none"})


if __name__ == "__main__":
    unittest.main()
