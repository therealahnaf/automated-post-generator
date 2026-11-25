import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

from tools.news import telegram_codex_watcher as watcher
from tools.news_v2 import brief, headline, pipeline


def picture(path: Path, color, size=(1200, 900)) -> Path:
    import random

    rng = random.Random(sum(color))
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    for _ in range(12):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        draw.rectangle((x, y, x + rng.randrange(80, 400), y + rng.randrange(80, 400)),
                       fill=tuple(rng.randrange(256) for _ in range(3)))
    image.save(path)
    return path


GOOD_BRIEF = {
    "summary": "NVIDIA's board reinstated Jensen Huang as CEO after an internal review, effective immediately.",
    "facts": [{"fact": "The board voted unanimously to reinstate Huang.", "source": "https://nvidianews.nvidia.com/x"}],
    "source_urls": ["https://nvidianews.nvidia.com/x"],
    "media_pages": ["https://nvidianews.nvidia.com/bios/jensen-huang"],
}


class BriefTests(unittest.TestCase):
    def test_a_complete_brief_is_valid(self) -> None:
        self.assertEqual(brief.problems(GOOD_BRIEF), [])

    def test_problems_are_all_reported(self) -> None:
        issues = brief.problems({"summary": "short", "facts": [{"fact": "x", "source": "nowhere"}],
                                 "source_urls": ["not a url"], "badge": "OFFICIAL"})
        self.assertEqual(len(issues), 5)


class HeadlineCheckTests(unittest.TestCase):
    def test_good_headlines_pass(self) -> None:
        source = "NVIDIA reinstated Jensen Huang"
        self.assertEqual(headline.check("NVIDIA Reinstates Jensen Huang as CEO", source), [])
        self.assertEqual(headline.check("Qwen3.8 27B Jumps from 82 to 382 Tokens per Second on RTX 3090", ""), [])

    def test_clickbait_and_hedges_fail(self) -> None:
        self.assertIn("no exclamation or question marks", headline.check("Is This the End of GPUs?", ""))
        self.assertIn("opens with a hedge", headline.check("A Post Claims Nvidia Cards Hide 80GB", ""))
        self.assertIn("banned clickbait phrase", headline.check("You Won't Believe What This GPU Does", ""))
        self.assertTrue(any(issue.startswith("all-caps") for issue in headline.check("Nvidia Card Is MASSIVE Now", "")))
        self.assertIn("needs 3-16 words", headline.check("Huge", ""))


def make_job(root: Path) -> pipeline.Job:
    root.mkdir(parents=True, exist_ok=True)
    job = pipeline.Job(root)
    (root / "media").mkdir()
    photo = picture(root / "media" / "photo-1.png", (30, 120, 60))
    job.tweet.write_text(json.dumps({
        "requested_urls": ["https://x.com/nvidianewsroom/status/2124865060397019480"],
        "workflow_type": "news", "headline_highlight": "red", "post_language": "english",
        "platform_languages": {"facebook": "english", "instagram": "english"},
        "facebook_language": "english", "instagram_language": "english",
        "items": [{"id": "2124865060397019480", "text": "NVIDIA Board reinstates Jensen Huang.",
                   "url": "https://x.com/nvidianewsroom/status/2124865060397019480",
                   "downloaded_photos": [{"local_path": "media/photo-1.png"}]}],
    }), encoding="utf-8")
    job.brief.write_text(json.dumps(GOOD_BRIEF), encoding="utf-8")
    job.headline.write_text(json.dumps({"headline": "NVIDIA Reinstates Jensen Huang as CEO"}), encoding="utf-8")
    job.finalized.write_text("NVIDIA's board reinstated Jensen Huang as CEO.\n\nSources:\nNVIDIA Newsroom\n",
                             encoding="utf-8")
    job.sources.write_text(json.dumps({"urls": ["https://nvidianews.nvidia.com/x"]}), encoding="utf-8")
    job.source_media.mkdir()
    portrait = picture(job.source_media / "s1.png", (150, 40, 40), (1200, 1200))
    job.source_media.joinpath("source-media.json").write_text(json.dumps({
        "tweet_photos": [{"id": "t1", "path": str(photo), "size": [1200, 900], "logo": False, "similar_to": []}],
        "images": [{"id": "s1", "path": str(portrait), "publisher": "NVIDIA Newsroom", "caption": "Jensen Huang",
                    "alt": "", "size": [1200, 1200], "logo": False, "similar_to": []}],
        "quotes": [{"id": "q1", "text": "The Board believes Jensen is the right person to lead NVIDIA.",
                    "publisher": "NVIDIA Newsroom"}],
    }), encoding="utf-8")
    job.plan.write_text(json.dumps({"cover": {"image": "s1", "fill": True},
                                    "slides": [{"image": "t1", "text": True}, {"quote": "q1"}]}), encoding="utf-8")
    job.copy.write_text(json.dumps({"workflow_type": "news", "copy_language": "english", "mode": "plan",
                                    "secondary_source_images": [], "primary_feature_image": None,
                                    "short_descriptions": ["The board voted unanimously after an internal review."]}),
                        encoding="utf-8")
    return job


class PipelineTests(unittest.TestCase):
    def test_rebuild_renders_from_the_job_inputs_and_sends_the_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = make_job(root / "job")
            receipt = root / "receipt.json"
            environment = {"BITS_TODAY_JOB_DIR": str(job.dir), "BITS_TODAY_DEV_DIR": str(root / "dev"),
                           "TELEGRAM_PREVIEW_RECEIPT_PATH": str(receipt), "TELEGRAM_WATCHER_JOB_ID": "900001"}
            with patch.dict(os.environ, environment):
                self.assertEqual(pipeline.main(["rebuild"]), 0)
            carousel = json.loads((job.package / "carousel.json").read_text(encoding="utf-8"))
            self.assertEqual(len(carousel["secondary_images"]), 2)
            self.assertEqual(carousel["credits"], ["Image via X", "Source: NVIDIA Newsroom"])
            self.assertTrue(receipt.is_file())
            preview = next((root / "dev" / "previews").iterdir())
            self.assertEqual(len(list(preview.glob("*.png"))), 3)
            self.assertEqual(len(job.state()["slides"]), 3)

    def test_rebuild_rejects_an_invalid_plan_with_the_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = make_job(root / "job")
            job.plan.write_text(json.dumps({"cover": "s9", "slides": []}), encoding="utf-8")
            with patch.dict(os.environ, {"BITS_TODAY_JOB_DIR": str(job.dir), "BITS_TODAY_DEV_DIR": str(root / "dev")}):
                self.assertEqual(pipeline.main(["rebuild"]), 1)

    def test_publish_reuses_facebook_hosted_images_for_instagram(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = pipeline.Job(root)
            job.package.mkdir()
            job.captions.mkdir()
            (job.package / "carousel.json").write_text(json.dumps({"primary_image": "a.png",
                                                                   "secondary_images": ["b.png"]}), encoding="utf-8")
            for name in ("facebook", "instagram"):
                (job.captions / f"{name}-description.txt").write_text("Caption", encoding="utf-8")
            calls = []

            def fake_run(command, **_):
                calls.append(command)
                if "publish_facebook.py" in command[1]:
                    out = {"facebook_image_urls": ["https://fb/1", "https://fb/2"], "facebook_permalink": "https://fb/post"}
                elif "publish_instagram.py" in command[1]:
                    out = {"instagram_media_id": "17900", "instagram_permalink": "https://ig/p/1"}
                else:
                    out = {}
                return subprocess.CompletedProcess(command, 0, json.dumps(out, indent=2), "")

            final = root / "final.txt"
            with patch.dict(os.environ, {"BITS_TODAY_JOB_DIR": str(root), "BITS_TODAY_FINAL_OUTPUT": str(final)}), \
                    patch.object(pipeline.subprocess, "run", side_effect=fake_run):
                self.assertEqual(pipeline.main(["publish"]), 0)
            instagram = next(command for command in calls if "publish_instagram.py" in command[1])
            self.assertEqual(instagram[instagram.index("--image-url") + 1], "https://fb/1")
            self.assertEqual(instagram[instagram.index("--secondary-image-url") + 1], "https://fb/2")
            self.assertIn("https://ig/p/1", final.read_text(encoding="utf-8"))

    def test_publish_reports_a_partial_failure_without_retrying_facebook(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = pipeline.Job(root)
            job.package.mkdir()
            job.captions.mkdir()
            (job.package / "carousel.json").write_text(json.dumps({"primary_image": "a.png", "secondary_images": []}),
                                                      encoding="utf-8")
            for name in ("facebook", "instagram"):
                (job.captions / f"{name}-description.txt").write_text("Caption", encoding="utf-8")
            stages = []

            def fake_run(command, **_):
                if "report_progress.py" in command[1]:
                    stages.append(command[command.index("--stage") + 1])
                    return subprocess.CompletedProcess(command, 0, "", "")
                if "publish_facebook.py" in command[1]:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"facebook_image_urls": ["https://fb/1"],
                                                                               "facebook_permalink": "https://fb/p"}), "")
                return subprocess.CompletedProcess(command, 1, "", "Instagram container timed out")

            final = root / "final.txt"
            with patch.dict(os.environ, {"BITS_TODAY_JOB_DIR": str(root), "BITS_TODAY_FINAL_OUTPUT": str(final)}), \
                    patch.object(pipeline.subprocess, "run", side_effect=fake_run):
                self.assertEqual(pipeline.main(["publish"]), 0)
            self.assertEqual(stages[-1], "failed")
            self.assertIn("Facebook published: https://fb/p", final.read_text(encoding="utf-8"))


class DescriptionStepTests(unittest.TestCase):
    def test_description_calls_the_real_function_signature(self) -> None:
        from tools.news import generate_description as news_description

        with tempfile.TemporaryDirectory() as directory:
            job = make_job(Path(directory) / "job")
            calls = []

            def fake_run(command, **_):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(news_description, "generate_description", autospec=True,
                              return_value="NVIDIA reinstated Huang.") as generate,                     patch.object(news_description, "require_api_key"),                     patch.object(news_description, "make_client"),                     patch.object(pipeline.subprocess, "run", side_effect=fake_run):
                pipeline.write_description(job, GOOD_BRIEF)
            self.assertEqual(generate.call_args.kwargs["max_output_tokens"], pipeline.DESCRIPTION_TOKENS)
            self.assertIn("Verified context from research", generate.call_args.args[1])
            self.assertEqual(job.description.read_text(encoding="utf-8").strip(), "NVIDIA reinstated Huang.")
            finalize = next(command for command in calls if "finalize_description.py" in command[1])
            self.assertIn("https://x.com/nvidianewsroom/status/2124865060397019480", finalize)

    def test_an_unexpected_error_reports_failed_with_the_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            final = root / "final.txt"
            stages = []

            def fake_progress(job, stage, detail=None):
                stages.append((stage, detail))

            with patch.dict(os.environ, {"BITS_TODAY_JOB_DIR": str(root), "BITS_TODAY_FINAL_OUTPUT": str(final)}),                     patch.object(pipeline, "progress", side_effect=fake_progress),                     patch.object(pipeline, "command_publish", side_effect=TypeError("boom")):
                self.assertEqual(pipeline.main(["publish"]), 1)
            self.assertEqual(stages[-1][0], "failed")
            self.assertIn("TypeError: boom", final.read_text(encoding="utf-8"))


class WatcherRoutingTests(unittest.TestCase):
    def config(self, root: Path) -> watcher.Config:
        return watcher.Config("token", "1", frozenset(), root, root / "state", root / "state" / "db.sqlite3", Path("codex"))

    def job(self, workflow: str = "news", session: str | None = None) -> dict:
        return {"id": 7, "workflow_type": workflow, "request_text": "https://x.com/a/status/123", "turn_count": 1,
                "session_id": session}

    def test_selection_is_recorded_per_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.config(root)
            job_dir = root / "job-7"
            job_dir.mkdir()
            self.assertEqual(watcher.news_pipeline_for(config, self.job(), job_dir, first_turn=True), "v1")
            (root / ".automation").mkdir()
            (root / ".automation" / "news-pipeline").write_text("v2\n", encoding="utf-8")
            # Already recorded as v1, so switching mid-job changes nothing.
            self.assertEqual(watcher.news_pipeline_for(config, self.job(), job_dir, first_turn=False), "v1")
            fresh = root / "job-8"
            fresh.mkdir()
            self.assertEqual(watcher.news_pipeline_for(config, self.job(), fresh, first_turn=True), "v2")
            self.assertEqual(watcher.news_pipeline_for(config, self.job("model"), fresh, first_turn=True), "v1")

    def test_v2_jobs_run_the_pipeline_and_report_its_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.config(root)
            (root / ".automation").mkdir()
            (root / ".automation" / "news-pipeline").write_text("v2\n", encoding="utf-8")
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                state = Path(kwargs["env"]["BITS_TODAY_JOB_DIR"]) / "v2-state.json"
                state.write_text(json.dumps({"session_id": "thread-1"}), encoding="utf-8")
                return SimpleNamespace(returncode=0)

            with patch.object(watcher.subprocess, "run", side_effect=fake_run):
                result = watcher.invoke_codex(config, self.job(), prompt="ignored", reply_to_message_id=5, resume=False)
                self.assertEqual(result.session_id, "thread-1")
                self.assertEqual(commands[-1][2:], ["start", "--url", "https://x.com/a/status/123"])
                watcher.invoke_codex(config, self.job(session="thread-1"), prompt="the revision prompt",
                                     reply_to_message_id=6, resume=True, feedback="Use the portrait")
                self.assertEqual(commands[-1][2:], ["revise", "--feedback", "Use the portrait"])
                watcher.invoke_codex(config, self.job(session="thread-1"), prompt="yes", reply_to_message_id=7,
                                     resume=True)
                self.assertEqual(commands[-1][2:], ["publish"])


if __name__ == "__main__":
    unittest.main()
