import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tools.news import dev_mode, notify_telegram, publish_facebook, publish_instagram, report_progress
from tools.reels import publish_facebook_reel, publish_instagram_reel


class DevModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.dev = self.root / "dev"
        self.environment = patch.dict(os.environ, {dev_mode.ENV: str(self.dev)})
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.directory.cleanup()

    def test_preview_is_saved_locally_with_a_receipt_and_never_sent(self) -> None:
        primary, secondary = self.root / "01.png", self.root / "02.png"
        for path in (primary, secondary):
            Image.new("RGB", (1080, 1350), (20, 20, 20)).save(path)
        caption = self.root / "caption.txt"
        caption.write_text("A caption.", encoding="utf-8")
        receipt = self.root / "receipt.json"
        with patch.dict(os.environ, {"TELEGRAM_PREVIEW_RECEIPT_PATH": str(receipt), "TELEGRAM_WATCHER_JOB_ID": "900001"}), \
                patch.object(notify_telegram, "load_config", side_effect=AssertionError("Telegram must not be configured")), \
                patch.object(notify_telegram.requests, "Session", side_effect=AssertionError("no network in dev")):
            code = notify_telegram.main(["--image", str(primary), "--secondary-image", str(secondary),
                                         "--description-file", str(caption), "--stage", "preview", "--send",
                                         "--platform", "both"])
        self.assertEqual(code, 0)
        folders = list((self.dev / "previews").iterdir())
        self.assertEqual(len(folders), 1)
        self.assertEqual(sorted(path.name for path in folders[0].glob("*.png")), ["01.png", "02.png"])
        self.assertEqual((folders[0] / "caption.txt").read_text(encoding="utf-8"), "A caption.")
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(payload["job_id"], "900001")
        self.assertEqual(len(payload["images"]), 2)

    def test_progress_goes_to_the_local_log(self) -> None:
        with patch.object(report_progress, "load_config", side_effect=AssertionError("no watcher database in dev")):
            code = report_progress.main(["--job-id", "900001", "--stage", "headline", "--detail", "A headline"])
        self.assertEqual(code, 0)
        rows = [json.loads(line) for line in (self.dev / "progress.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["stage"], "headline")
        self.assertEqual(rows[0]["detail"], "A headline")

    def test_every_publisher_refuses(self) -> None:
        image = self.root / "01.png"
        Image.new("RGB", (1080, 1350)).save(image)
        caption = self.root / "caption.txt"
        caption.write_text("A caption.", encoding="utf-8")
        with patch("requests.post", side_effect=AssertionError("no publishing in dev")), \
                patch("requests.get", side_effect=AssertionError("no publishing in dev")):
            self.assertEqual(publish_facebook.main(["--image", str(image), "--message-file", str(caption),
                                                    "--publish", "--confirm", "yes"]), 2)
            self.assertEqual(publish_instagram.main(["--image-url", "https://example.com/a.png", "--caption-file",
                                                     str(caption), "--publish", "--confirm", "yes"]), 2)
            self.assertEqual(publish_facebook_reel.main(["--video", str(image), "--description-file", str(caption),
                                                         "--publish", "--confirm", "yes"]), 2)
            self.assertEqual(publish_instagram_reel.main(["--video", str(image), "--caption-file", str(caption),
                                                          "--publish", "--confirm", "yes"]), 2)


if __name__ == "__main__":
    unittest.main()
