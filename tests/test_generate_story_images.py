import base64
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from experiments.drama3_tts import generate_story_images as images


def png(color):
    output = io.BytesIO()
    Image.new("RGB", (8, 10), color).save(output, format="PNG")
    return output.getvalue()


def response(data):
    result = Mock(status_code=200, headers={"X-Generation-Id": "test-generation"})
    result.json.return_value = {"data": [{"b64_json": base64.b64encode(data).decode()}], "usage": {"cost": 0.01}}
    return result


class GenerateStoryImagesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.story = self.root / "story.txt"
        self.story.write_text("[curious] First beat. [serious] Second beat. [hopeful] Third beat.", encoding="utf-8")
        self.reference = self.root / "reference.png"
        self.reference.write_bytes(png("white"))
        self.output = self.root / "output"
        self.environment = patch.dict(images.os.environ, {"OPENROUTER_API_KEY": "test-key"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_all_tag_boundaries_preserve_text_and_restore_paired_delivery(self):
        story = "Opening. [curious] Hello <whisper>quietly</whisper> again. [short pause][laughs] End."
        segments = images.split_story(story)
        self.assertEqual("".join(item["raw_text"] for item in segments), story)
        self.assertEqual(len(segments), 6)
        self.assertEqual(segments[2]["active_tags"], ["[curious]", "<whisper>"])
        self.assertEqual(segments[3]["active_tags"], ["[curious]"])
        self.assertTrue(segments[4]["tag_only"])
        self.assertEqual(segments[5]["text"], "End.")

    def test_every_scene_uses_only_original_reference_then_resumes_without_calls(self):
        generated = [png("red"), png("green"), png("blue")]
        with patch.object(images.requests, "post", side_effect=[response(data) for data in generated]) as post, redirect_stderr(io.StringIO()):
            result = images.run(self.story, self.reference, self.output)
        self.assertEqual(post.call_count, 3)
        expected = [[self.reference.read_bytes()]] * 3
        for call, references in zip(post.call_args_list, expected):
            payload = call.kwargs["json"]
            self.assertEqual(payload["model"], "qwen/qwen-image-2.1-turbo")
            actual = [base64.b64decode(item["image_url"]["url"].split(",", 1)[1]) for item in payload["input_references"]]
            self.assertEqual(actual, references)
            self.assertEqual(payload["n"], 1)
        self.assertTrue(all(item["status"] == "completed" for item in result["segments"]))
        self.assertTrue(all(item["reference_segments"] == [] for item in result["segments"]))
        self.assertEqual(result["settings"]["reference_mode"], "original_style_only")
        with patch.object(images.requests, "post") as post:
            images.run(self.story, self.reference, self.output)
        post.assert_not_called()

    def test_failed_second_request_preserves_first_and_requires_explicit_retry(self):
        error = Mock(status_code=503, text="upstream unavailable")
        with patch.object(images.requests, "post", side_effect=[response(png("red")), error]), redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
                images.run(self.story, self.reference, self.output)
        document = json.loads((self.output / "segments.json").read_text())
        self.assertEqual([item["status"] for item in document["segments"]], ["completed", "failed", "pending"])
        with patch.object(images.requests, "post") as post:
            with self.assertRaisesRegex(RuntimeError, "--retry-failed"):
                images.run(self.story, self.reference, self.output)
        post.assert_not_called()
        with patch.object(images.requests, "post", side_effect=[response(png("green")), response(png("blue"))]) as post, redirect_stderr(io.StringIO()):
            result = images.run(self.story, self.reference, self.output, retry_failed=True)
        self.assertEqual(post.call_count, 2)
        self.assertTrue(all(item["status"] == "completed" for item in result["segments"]))

    def test_dry_run_does_not_call_api_and_changed_story_cannot_resume_old_sequence(self):
        with patch.object(images.requests, "post") as post:
            document = images.run(self.story, self.reference, self.output, dry_run=True)
        post.assert_not_called()
        self.assertEqual(len(document["segments"]), 3)
        self.story.write_text("[curious] Another story.", encoding="utf-8")
        with patch.object(images.requests, "post") as post:
            with self.assertRaisesRegex(ValueError, "changed"):
                images.run(self.story, self.reference, self.output)
        post.assert_not_called()

    def test_missing_close_tag_prevents_generation(self):
        self.story.write_text("[curious] A <whisper>missing close.", encoding="utf-8")
        with patch.object(images.requests, "post") as post:
            with self.assertRaisesRegex(ValueError, "Unclosed"):
                images.run(self.story, self.reference, self.output)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
