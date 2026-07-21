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

    def test_gpt5_description_uses_minimal_reasoning(self) -> None:
        client = FakeClient(["A generated description."])

        description = generate_description.generate_description(
            client,
            "Source text.",
            model="gpt-5-mini",
            max_output_tokens=500,
        )

        self.assertEqual(description, "A generated description.")
        self.assertEqual(
            client.responses.calls[0]["reasoning"],
            {"effort": "minimal"},
        )

    def test_description_retries_empty_response_with_larger_budget(self) -> None:
        client = FakeClient(["", "A generated description after retry."])

        description = generate_description.generate_description(
            client,
            "Source text.",
            model="gpt-5-mini",
            max_output_tokens=500,
        )

        self.assertEqual(description, "A generated description after retry.")
        self.assertEqual(
            [call["max_output_tokens"] for call in client.responses.calls],
            [500, 1500],
        )


if __name__ == "__main__":
    unittest.main()
