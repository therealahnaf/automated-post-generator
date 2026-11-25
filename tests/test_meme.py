import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from tools.news import telegram_codex_watcher as watcher
from tools.viral import meme_copy, meme_pipeline, x_post_card


def status(quote: dict | None = None) -> dict:
    value = {"id": "123", "url": "https://x.com/dev/status/123", "text": "do y'all remember when ai looked like this",
             "author": {"name": "Dev", "screen_name": "dev", "avatar_url": "", "verification": {"verified": True}},
             "created_timestamp": 1793556886, "views": 1800000, "likes": 67431, "replies": 503, "reposts": 3000,
             "bookmarks": 7300, "media": {"all": [{"type": "photo", "url": "https://pbs.twimg.com/media/a.jpg"}]}}
    if quote:
        value["quote"] = quote
    return value


def make_job(root: Path) -> meme_pipeline.Job:
    root.mkdir(parents=True, exist_ok=True)
    job = meme_pipeline.Job(root)
    job.media.mkdir()
    photo = job.media / "123-photo-1.jpg"
    Image.new("RGB", (1200, 800), (40, 120, 90)).save(photo)
    item = status()
    item["downloaded_media"] = [{"source_url": "https://pbs.twimg.com/media/a.jpg", "local_path": str(photo)}]
    job.tweet.write_text(json.dumps({"requested_urls": [item["url"]], "workflow_type": "meme", "items": [item]}),
                         encoding="utf-8")
    job.copy.write_text(json.dumps({"title": "Remember When AI Turned Everything Into Dogs",
                                    "explanation": "In 2015 DeepDream put dog faces in every photo.",
                                    "hashtags": ["AI", "DeepDream"]}), encoding="utf-8")
    return job


class MemeCopyTests(unittest.TestCase):
    def test_person_openers_pass_but_hedges_and_bait_fail(self) -> None:
        self.assertEqual(meme_copy.check_title("Someone Found Two Neurons That Turn Qwen Evil", ""), [])
        self.assertIn("opens with a hedge", meme_copy.check_title("A Post Claims Qwen Is Evil", ""))
        self.assertIn("bait phrase", meme_copy.check_title("Wait For It, Qwen Goes Evil", ""))

    def test_exact_feedback_sets_the_text_without_a_model_call(self) -> None:
        previous = {"title": "Old Title Here", "explanation": "Old caption.", "hashtags": ["AI"]}
        result = meme_copy.generate(None, status(), Path("."), feedback="title: Someone Made Qwen Evil", previous=previous)
        self.assertEqual(result["title"], "Someone Made Qwen Evil")
        self.assertEqual(result["explanation"], "Old caption.")
        result = meme_copy.generate(None, status(), Path("."), feedback="caption: New caption text.", previous=previous)
        self.assertEqual(result["explanation"], "New caption text.")

    def test_credit_names_the_poster_and_any_other_quoted_poster(self) -> None:
        self.assertEqual(meme_copy.credit_line(status()), "Post by @dev on X")
        other = status({"author": {"screen_name": "lyra"}, "text": "evil qwen"})
        self.assertEqual(meme_copy.credit_line(other), "Post by @dev, quoting @lyra on X")
        own = status({"author": {"screen_name": "Dev"}, "text": "earlier"})
        self.assertEqual(meme_copy.credit_line(own), "Post by @dev on X")
        caption = meme_copy.caption_text({"explanation": "Context.", "hashtags": ["AI", "Qwen"]}, other)
        self.assertEqual(caption, "Context.\n\nPost by @dev, quoting @lyra on X\n\n#AI #Qwen\n")


class MemePipelineTests(unittest.TestCase):
    def test_build_renders_one_card_and_sends_the_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = make_job(root / "job")
            receipt = root / "receipt.json"
            environment = {"BITS_TODAY_JOB_DIR": str(job.dir), "BITS_TODAY_DEV_DIR": str(root / "dev"),
                           "TELEGRAM_PREVIEW_RECEIPT_PATH": str(receipt), "TELEGRAM_WATCHER_JOB_ID": "900002"}
            with patch.dict(os.environ, environment), \
                    patch.object(x_post_card, "avatar", return_value=Image.new("RGB", (400, 400), (90, 90, 90))):
                meme_pipeline.build(job)
            with Image.open(job.card) as card:
                self.assertEqual(card.size, (1080, 1350))
            caption = (job.captions / "instagram-description.txt").read_text(encoding="utf-8")
            self.assertIn("Post by @dev on X", caption)
            self.assertNotIn("link in bio", caption)
            self.assertTrue(receipt.is_file())
            self.assertEqual(job.state()["title"], "Remember When AI Turned Everything Into Dogs")

    def test_publish_posts_one_image_to_both_platforms_without_the_website(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = make_job(root / "job")
            job.captions.mkdir()
            for platform in ("facebook", "instagram"):
                (job.captions / f"{platform}-description.txt").write_text("Caption", encoding="utf-8")
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                if "publish_facebook.py" in command[1]:
                    output = {"facebook_post_id": "1_2", "facebook_image_urls": ["https://fb.example/a.jpg"]}
                else:
                    output = {"instagram_media_id": "9", "instagram_permalink": "https://instagram.com/p/x"}
                return SimpleNamespace(returncode=0, stdout=json.dumps(output), stderr="")

            final = root / "final.txt"
            with patch.dict(os.environ, {"BITS_TODAY_JOB_DIR": str(job.dir), "BITS_TODAY_FINAL_OUTPUT": str(final)}), \
                    patch.object(meme_pipeline.subprocess, "run", side_effect=fake_run), \
                    patch.object(meme_pipeline, "progress"):
                self.assertEqual(meme_pipeline.main(["publish"]), 0)
            facebook, instagram = commands
            self.assertIn("--skip-website", facebook)
            self.assertNotIn("--secondary-image", facebook)
            self.assertIn("--skip-website", instagram)
            self.assertEqual(instagram[instagram.index("--image-url") + 1], "https://fb.example/a.jpg")
            self.assertIn("Instagram: https://instagram.com/p/x", final.read_text(encoding="utf-8"))


class MemeWatcherTests(unittest.TestCase):
    def test_the_meme_button_runs_the_meme_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = watcher.Config("token", "1", frozenset(), root, root / "state", root / "state" / "db.sqlite3",
                                    Path("codex"))
            job = {"id": 9, "workflow_type": "meme", "request_text": "https://x.com/a/status/123", "turn_count": 1,
                   "session_id": None}
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                state = Path(kwargs["env"]["BITS_TODAY_JOB_DIR"]) / "meme-state.json"
                state.write_text(json.dumps({"session_id": "meme-job-9"}), encoding="utf-8")
                return SimpleNamespace(returncode=0)

            with patch.object(watcher.subprocess, "run", side_effect=fake_run):
                result = watcher.invoke_codex(config, job, prompt="ignored", reply_to_message_id=5, resume=False)
                self.assertEqual(result.session_id, "meme-job-9")
                self.assertTrue(commands[-1][1].endswith(os.path.join("viral", "meme_pipeline.py")))
                self.assertEqual(commands[-1][2:], ["start", "--url", "https://x.com/a/status/123"])
                watcher.invoke_codex(config, {**job, "session_id": "meme-job-9"}, prompt="revision prompt",
                                     reply_to_message_id=6, resume=True, feedback="title: Something Better")
                self.assertEqual(commands[-1][2:], ["revise", "--feedback", "title: Something Better"])
                watcher.invoke_codex(config, {**job, "session_id": "meme-job-9"}, prompt="yes", reply_to_message_id=7,
                                     resume=True)
                self.assertEqual(commands[-1][2:], ["publish"])
        self.assertIn("meme", watcher.WORKFLOW_TYPES)


if __name__ == "__main__":
    unittest.main()
