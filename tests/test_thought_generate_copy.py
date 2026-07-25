import json
import unittest
from types import SimpleNamespace

from tools.thoughts import generate_copy


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


class FakeResponses:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output_text, output=[])


class FakeClient:
    def __init__(self, output_text: str) -> None:
        self.responses = FakeResponses(output_text)


class ThoughtGenerateCopyTests(unittest.TestCase):
    def test_parses_hook_and_flowing_paragraphs(self) -> None:
        hook, paragraphs = generate_copy.parse_copy(
            json.dumps(
                {
                    "hook": "Intelligence Is Cheap. Judgment Is Not.",
                    "paragraphs": PARAGRAPHS,
                }
            )
        )
        self.assertEqual(hook, "Intelligence Is Cheap. Judgment Is Not.")
        self.assertEqual(paragraphs, PARAGRAPHS)

    def test_generation_uses_fixed_model_and_thread_prompt(self) -> None:
        client = FakeClient(
            json.dumps(
                {
                    "hook": "Intelligence Is Cheap. Judgment Is Not.",
                    "paragraphs": PARAGRAPHS,
                }
            )
        )
        hook, paragraphs = generate_copy.generate_copy(
            client,
            "Original tweet. Same-author thread continuation.",
        )
        self.assertTrue(hook)
        self.assertEqual(len(paragraphs), 3)
        call = client.responses.calls[0]
        self.assertEqual(call["model"], "gpt-5.6-luna")
        self.assertEqual(call["reasoning"], {"effort": "none"})
        self.assertIn(
            "Same-author thread continuation",
            call["input"][1]["content"],
        )
        self.assertIn(
            "major public figure or major official account",
            call["input"][0]["content"],
        )

    def test_rejects_too_few_paragraphs(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "3–8"):
            generate_copy.parse_copy(
                json.dumps(
                    {
                        "hook": "A valid thoughtful headline appears here",
                        "paragraphs": PARAGRAPHS[:2],
                    }
                )
            )


if __name__ == "__main__":
    unittest.main()
