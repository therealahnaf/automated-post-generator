import json
import tempfile
import unittest
from pathlib import Path

import post_language


class PostLanguageTests(unittest.TestCase):
    def test_auto_choice_uses_only_supported_languages(self) -> None:
        selected = post_language.choose_post_language(
            "auto", chooser=lambda choices: choices[1]
        )
        self.assertEqual(selected, "bangla")

    def test_explicit_language_does_not_call_randomizer(self) -> None:
        selected = post_language.choose_post_language(
            "english", chooser=lambda _: self.fail("chooser should not run")
        )
        self.assertEqual(selected, "english")

    def test_reads_persisted_language_from_tweet_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tweet.json"
            path.write_text(json.dumps({"post_language": "bangla"}), encoding="utf-8")
            self.assertEqual(post_language.read_post_language(path), "bangla")

    def test_auto_highlight_choice_uses_supported_styles(self) -> None:
        selected = post_language.choose_headline_highlight(
            "auto", chooser=lambda choices: choices[0]
        )
        self.assertEqual(selected, "cyan")

    def test_reads_persisted_headline_highlight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tweet.json"
            path.write_text(
                json.dumps({"headline_highlight": "red"}), encoding="utf-8"
            )
            self.assertEqual(post_language.read_headline_highlight(path), "red")


if __name__ == "__main__":
    unittest.main()
