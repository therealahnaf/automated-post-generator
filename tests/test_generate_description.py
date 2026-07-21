import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import generate_description


class FakeResponses:
    def __init__(self, output_texts: list[str]) -> None:
        self.output_texts = output_texts
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.output_texts.pop(0)
        return SimpleNamespace(output_text=text, output=[])


class FakeClient:
    def __init__(self, output_texts: list[str]) -> None:
        self.responses = FakeResponses(output_texts)


class GenerateDescriptionTests(unittest.TestCase):
    def test_build_user_prompt_contains_few_shot_examples_and_source(self) -> None:
        source = "FT: China discussed possible AI data transfer restrictions."
        prompt = generate_description.build_user_prompt(source)

        self.assertIn("President Donald Trump has said", prompt)
        self.assertIn("The UK government is providing", prompt)
        self.assertIn("SOURCE START\nFT: China discussed", prompt)
        self.assertIn("Treat every story as consequential", prompt)
        self.assertIn("first sentence must be a high-stakes hook", prompt)
        self.assertIn("Do not invent catastrophe", prompt)
        self.assertIn("ignore the unfinished fragment", prompt)
        self.assertIn("under 1,300 characters", prompt)

    def test_build_bangla_prompt_requires_summary_without_extra_facts(self) -> None:
        prompt = generate_description.build_bangla_prompt(
            "Z.AI began operating a one-gigawatt data center."
        )

        self.assertIn("translation-summary", prompt)
        self.assertIn("Summarize rather than translating sentence by sentence", prompt)
        self.assertIn("700 characters", prompt)
        self.assertIn("Z.AI began operating", prompt)

    def test_read_tweet_text_uses_first_validated_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tweet.json"
            path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "2079424458075279484",
                                "text": "FT: China\u200b discussed\nAI data transfer curbs.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                generate_description.read_tweet_text(path),
                "FT: China discussed AI data transfer curbs.",
            )

    def test_read_tweet_text_rejects_empty_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tweet.json"
            path.write_text(
                json.dumps({"items": [{"id": "1", "text": "   "}] }),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                generate_description.read_tweet_text(path)

    def test_description_uses_fixed_luna_model_and_no_reasoning(self) -> None:
        client = FakeClient(["A generated description."])

        description = generate_description.generate_description(
            client,
            "Source text.",
            max_output_tokens=500,
        )

        self.assertEqual(description, "A generated description.")
        self.assertEqual(
            client.responses.calls[0]["reasoning"],
            {"effort": "none"},
        )
        self.assertEqual(
            client.responses.calls[0]["model"],
            "gpt-5.6-luna",
        )

    def test_description_retries_empty_response_with_larger_budget(self) -> None:
        client = FakeClient(["", "A generated description after retry."])

        description = generate_description.generate_description(
            client,
            "Source text.",
            max_output_tokens=500,
        )

        self.assertEqual(description, "A generated description after retry.")
        self.assertEqual(
            [call["max_output_tokens"] for call in client.responses.calls],
            [500, 1500],
        )

    def test_bangla_summary_uses_fixed_luna_model_and_no_reasoning(self) -> None:
        client = FakeClient(["জেড ডট এআই একটি ডেটা সেন্টার চালু করেছে।"])

        summary = generate_description.generate_bangla_summary(
            client,
            "Z.AI started operating a data center.",
            max_output_tokens=400,
        )

        self.assertIn("ডেটা সেন্টার", summary)
        call = client.responses.calls[0]
        self.assertEqual(call["reasoning"], {"effort": "none"})
        self.assertEqual(call["model"], "gpt-5.6-luna")
        self.assertEqual(
            call["input"][0]["content"],
            generate_description.BANGLA_SYSTEM_INSTRUCTIONS,
        )

    def test_bangla_summary_retries_non_bangla_response(self) -> None:
        client = FakeClient(
            [
                "Z.AI started a data center.",
                "জেড ডট এআই একটি ডেটা সেন্টার চালু করেছে।",
            ]
        )

        summary = generate_description.generate_bangla_summary(
            client,
            "Z.AI started operating a data center.",
            max_output_tokens=300,
        )

        self.assertTrue(generate_description.contains_bangla_text(summary))
        self.assertEqual(
            [call["max_output_tokens"] for call in client.responses.calls],
            [300, 800],
        )

    def test_combines_english_and_bangla_with_simple_separator(self) -> None:
        combined = generate_description.combine_descriptions(
            "English description.",
            "বাংলা বিবরণ।",
        )

        self.assertEqual(
            combined,
            "English description.\n\n---\n\nবাংলা বিবরণ।",
        )

    def test_rejects_combined_description_over_platform_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "maximum is 2200"):
            generate_description.combine_descriptions(
                "E" * 2000,
                "বাংলা " * 100,
            )

    def test_bilingual_generation_makes_two_model_calls(self) -> None:
        client = FakeClient(
            [
                "English description.",
                "সংক্ষিপ্ত বাংলা বিবরণ।",
            ]
        )

        combined = generate_description.generate_bilingual_description(
            client,
            "Source text.",
            description_max_output_tokens=500,
            translation_max_output_tokens=300,
        )

        self.assertEqual(len(client.responses.calls), 2)
        self.assertEqual(
            [call["model"] for call in client.responses.calls],
            ["gpt-5.6-luna", "gpt-5.6-luna"],
        )
        self.assertEqual(
            combined,
            "English description.\n\n---\n\nসংক্ষিপ্ত বাংলা বিবরণ।",
        )
        self.assertEqual(
            client.responses.calls[0]["input"][0]["content"],
            generate_description.SYSTEM_INSTRUCTIONS,
        )
        self.assertEqual(
            client.responses.calls[1]["input"][0]["content"],
            generate_description.BANGLA_SYSTEM_INSTRUCTIONS,
        )


if __name__ == "__main__":
    unittest.main()
