import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from tools.collections import collection_copy, cover, pipeline, slides, store, telegram_ui
from tools.news import notify_telegram, publish_instagram
from tools.news import telegram_codex_watcher as watcher


def fetched(root: Path, tweet_id: str, handle: str, text: str, photos: int = 0, video: bool = False) -> Path:
    """A folder shaped like collect.py's staging folder: tweet.json, media/, videos/."""
    folder = root / f"staging-{tweet_id}"
    (folder / "media").mkdir(parents=True)
    entries, downloaded = [], []
    for index in range(photos):
        path = folder / "media" / f"{tweet_id}-photo-{index + 1}.jpg"
        Image.new("RGB", (1200, 900), (30 * index + 40, 90, 120)).save(path)
        url = f"https://pbs.twimg.com/media/{tweet_id}-{index}.jpg"
        entries.append({"type": "photo", "url": url})
        downloaded.append({"source_url": url, "local_path": str(path)})
    if video:
        (folder / "videos").mkdir()
        (folder / "videos" / f"{tweet_id}-video-1.mp4").write_bytes(b"fake")
        entries.append({"type": "video", "url": f"https://video.twimg.com/{tweet_id}.mp4"})
    status = {"id": tweet_id, "url": f"https://x.com/{handle}/status/{tweet_id}", "text": text,
              "author": {"name": handle, "screen_name": handle}, "media": {"all": entries},
              "downloaded_media": downloaded}
    (folder / "tweet.json").write_text(json.dumps({"items": [status]}), encoding="utf-8")
    return folder


class CollectionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {"BITS_TODAY_COLLECTIONS_DIR": str(self.root / "collections")})
        self.environment.start()
        self.connection = store.connect()

    def tearDown(self) -> None:
        self.connection.close()
        self.environment.stop()
        self.temp.cleanup()

    def collection(self) -> store.Collection:
        collection = store.create(self.connection, "Jensen saga")
        posts = [("1", "nvidianewsroom", "NVIDIA Board reinstates Jensen Huang as CEO.", 0, False),
                 ("2", "Kalshi", "BREAKING: " + "a long line of claims about the board " * 4, 2, False),
                 ("3", "iTradeContracts", "Jensen is tweaking out", 0, True),
                 ("4", "The_Real_Fly", "Favorite to replace Jensen", 1, False)]
        for tweet_id, handle, text, photos, video in posts:
            store.add(self.connection, collection.id, tweet_id, f"https://x.com/{handle}/status/{tweet_id}",
                      fetched(self.root, tweet_id, handle, text, photos, video))
        return collection


class StoreTests(CollectionTestCase):
    def test_items_keep_order_and_can_be_dropped_moved_and_renamed(self) -> None:
        collection = self.collection()
        self.assertEqual([item.tweet_id for item in store.items(self.connection, collection.id)], ["1", "2", "3", "4"])
        store.move(self.connection, collection.id, 4, 1)
        store.remove(self.connection, collection.id, [3])
        self.assertEqual([item.tweet_id for item in store.items(self.connection, collection.id)], ["4", "1", "3"])
        store.rename(self.connection, collection.id, "  Jensen   returns ")
        self.assertEqual(store.get(self.connection, collection.id).name, "Jensen returns")
        with self.assertRaises(ValueError):
            store.add(self.connection, collection.id, "4", "u", fetched(self.root, "4b", "x", "dup"))
        self.assertTrue(store.items(self.connection, collection.id)[0].tweet_json.is_file())


class PlanTests(CollectionTestCase):
    def test_the_three_types_choose_slides_as_documented(self) -> None:
        items = store.items(self.connection, self.collection().id)
        media, _ = slides.plan(items, "media")
        # Every post keeps its own slide (the cover only previews them); a post without media becomes a card.
        self.assertEqual([(slide.kind, slide.item.tweet_id) for slide in media],
                         [("card", "1"), ("photo", "2"), ("photo", "2"), ("video", "3"), ("photo", "4")])
        tweets, _ = slides.plan(items, "tweets")
        self.assertEqual([slide.kind for slide in tweets], ["card", "card", "card_video", "card"])
        combo, _ = slides.plan(items, "combo")
        # Kalshi's long text makes it a card; the short posts show their media.
        self.assertEqual([(slide.kind, slide.item.tweet_id) for slide in combo],
                         [("card", "1"), ("card", "2"), ("video", "3"), ("photo", "4")])

    def test_slides_stop_at_the_carousel_limit_and_name_what_was_left_out(self) -> None:
        collection = store.create(self.connection, "Many")
        for index in range(12):
            store.add(self.connection, collection.id, str(100 + index), "u",
                      fetched(self.root, str(100 + index), "a", "short", photos=1))
        planned, left_out = slides.plan(store.items(self.connection, collection.id), "media")
        self.assertEqual(len(planned) + 1, slides.MAX_SLIDES)
        self.assertEqual(left_out, [10, 11, 12])


class CoverTests(CollectionTestCase):
    def test_the_stack_follows_the_type(self) -> None:
        items = store.items(self.connection, self.collection().id)
        media, _ = slides.plan(items, "media")
        self.assertEqual([(slide.kind, slide.item.tweet_id) for slide in cover.pick("media", media)],
                         [("photo", "2"), ("video", "3"), ("photo", "4")])
        tweets, _ = slides.plan(items, "tweets")
        self.assertTrue(all(slide.kind in ("card", "card_video") for slide in cover.pick("tweets", tweets)))
        combo, _ = slides.plan(items, "combo")
        self.assertEqual([slide.kind for slide in cover.pick("combo", combo)], ["card", "video", "card"])

    def test_every_first_line_is_one_width_of_one_to_three_words(self) -> None:
        target = cover.cinema.load_face(cover.cinema.HEADLINE_FACE, cover.full_size()).getlength(cover.REFERENCE_LEAD)
        for title in ("Jensen Is Everywhere", "AI Agents Took Over Every Job On This Team",
                      "Superintelligence Researchers Respond To The Strange Jensen Rumors"):
            fitted = cover.fit_title(title)
            self.assertLessEqual(len(fitted.lead.split()), cover.FIRST_LINE_WORDS)
            self.assertAlmostEqual(fitted.lead_font.getlength(fitted.lead), target, delta=target * 0.03)
            self.assertEqual(" ".join([fitted.lead, *fitted.rest]).split(), title.split())

    def test_the_cover_renders_without_a_swipe_hint(self) -> None:
        items = store.items(self.connection, self.collection().id)
        planned, _ = slides.plan(items, "tweets")
        image = cover.render("The Strange Jensen Huang Rumors Spreading Across X", date(2026, 11, 27), "tweets",
                             items, planned, self.root, "collection-1")
        self.assertEqual(image.size, (cover.WIDTH, cover.HEIGHT))


class CopyTests(CollectionTestCase):
    def test_credit_names_every_poster_and_exact_feedback_needs_no_model(self) -> None:
        items = store.items(self.connection, self.collection().id)
        self.assertEqual(collection_copy.credit_line(items),
                         "Posts by @nvidianewsroom, @Kalshi, @iTradeContracts and @The_Real_Fly on X")
        previous = {"title": "Old", "explanation": "Old caption.", "hashtags": ["AI"]}
        result = collection_copy.generate(None, items, feedback="title: The Jensen Saga So Far", previous=previous)
        self.assertEqual(result["title"], "The Jensen Saga So Far")


class PipelineTests(CollectionTestCase):
    def job(self) -> pipeline.Job:
        job = pipeline.Job(self.root / "job")
        job.dir.mkdir()
        return job

    def edit(self, job: pipeline.Job, *words: str) -> int:
        with patch.dict(os.environ, {"BITS_TODAY_JOB_DIR": str(job.dir)}), patch.object(pipeline, "progress"):
            return pipeline.main(["edit", *words])

    def test_the_revision_tools_edit_posts_type_and_copy(self) -> None:
        collection = self.collection()
        job = self.job()
        job.save(collection_id=collection.id, type="media")
        job.copy.write_text(json.dumps({"title": "Old", "explanation": "Old caption.", "hashtags": ["AI"]}), encoding="utf-8")
        self.assertEqual(self.edit(job, "move", "1", "4"), 0)
        self.assertEqual([item.tweet_id for item in store.items(self.connection, collection.id)], ["2", "3", "4", "1"])
        # Slide numbers are one higher than post numbers (the cover is slide 1): an 8 here is refused, not fatal.
        self.assertEqual(self.edit(job, "move", "4", "8"), 2)
        self.assertEqual(self.edit(job, "drop", "9"), 2)
        self.assertEqual(self.edit(job, "drop", "2", "3"), 0)
        self.assertEqual([item.tweet_id for item in store.items(self.connection, collection.id)], ["2", "1"])
        self.assertEqual(self.edit(job, "type", "tweets"), 0)
        self.assertEqual(job.state()["type"], "tweets")
        self.assertEqual(self.edit(job, "title", "Here's All The Conjectures AI Disproved This Week"), 0)
        self.assertEqual(json.loads(job.copy.read_text(encoding="utf-8"))["title"],
                         "Here's All The Conjectures AI Disproved This Week")

    def test_a_revision_that_cannot_be_applied_resends_the_preview_instead_of_failing(self) -> None:
        collection = self.collection()
        job = self.job()
        job.save(collection_id=collection.id, type="media")
        job.copy.write_text(json.dumps({"title": "Old", "explanation": "x", "hashtags": []}), encoding="utf-8")
        environment = {"BITS_TODAY_JOB_DIR": str(job.dir), "BITS_TODAY_FINAL_OUTPUT": str(job.dir / "final.txt"),
                       "TELEGRAM_PREVIEW_RECEIPT_PATH": str(job.dir / "receipt.json")}
        with patch.dict(os.environ, environment), patch.object(pipeline, "progress"), \
                patch.object(pipeline, "codex_revision", side_effect=pipeline.StepError("the session exited")), \
                patch.object(pipeline, "build", return_value=[]) as build:
            self.assertEqual(pipeline.main(["revise", "--feedback", "move the deez notz one to the end"]), 0)
        build.assert_called_once()
        self.assertIn("Revision not applied", (job.dir / "final.txt").read_text(encoding="utf-8"))
        with patch.dict(os.environ, environment), patch.object(pipeline, "progress"), \
                patch.object(pipeline, "codex_revision") as session, patch.object(pipeline, "build", return_value=[]), \
                patch.object(pipeline, "write_copy") as write_copy:
            pipeline.main(["revise", "--feedback", "title: Exactly This"])
            pipeline.main(["revise", "--feedback", "type: tweets"])
        session.assert_not_called()
        write_copy.assert_called_once()
        self.assertEqual(job.state()["type"], "tweets")

    def test_publish_sends_photos_and_videos_to_instagram_only(self) -> None:
        collection = self.collection()
        job = self.job()
        slides_on_disk = [job.dir / "01.jpg", job.dir / "02.mp4", job.dir / "03.jpg"]
        for path in slides_on_disk:
            path.write_bytes(b"x")
        job.caption.parent.mkdir()
        job.caption.write_text("Caption", encoding="utf-8")
        job.save(collection_id=collection.id, type="media", slides=[str(path) for path in slides_on_disk])
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
                {"instagram_media_id": "9", "instagram_permalink": "https://instagram.com/p/x"}))

        final = self.root / "final.txt"
        with patch.dict(os.environ, {"BITS_TODAY_JOB_DIR": str(job.dir), "BITS_TODAY_FINAL_OUTPUT": str(final)}), \
                patch.object(pipeline, "stage", side_effect=lambda path: f"https://media.example/{path.name}"), \
                patch.object(pipeline.subprocess, "run", side_effect=fake_run), patch.object(pipeline, "progress"):
            self.assertEqual(pipeline.main(["publish"]), 0)
        (command,) = commands
        self.assertIn("publish_instagram.py", command[1])
        items = [command[index + 1] for index, value in enumerate(command) if value == "--carousel-item"]
        self.assertEqual(items, ["image=https://media.example/01.jpg", "video=https://media.example/02.mp4",
                                 "image=https://media.example/03.jpg"])
        self.assertIn("--skip-website", command)
        self.assertEqual(store.get(self.connection, collection.id).status, "posted")
        self.assertIn("Facebook and website: skipped", final.read_text(encoding="utf-8"))

    def test_the_request_names_the_collection_and_type(self) -> None:
        self.assertEqual(pipeline.parse_request("collection:7:combo"), (7, "combo"))


class PublisherTests(unittest.TestCase):
    def test_instagram_carousel_items_keep_their_kind_and_order(self) -> None:
        self.assertEqual(publish_instagram.parse_carousel_items(["image=https://a/1.jpg", "video=https://a/2.mp4"]),
                         [("image", "https://a/1.jpg"), ("video", "https://a/2.mp4")])
        with self.assertRaises(ValueError):
            publish_instagram.parse_carousel_items(["gif=https://a/1.gif", "image=https://a/2.jpg"])

    def test_the_album_preview_splits_photos_and_videos(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo, video = root / "01.jpg", root / "02.mp4"
            Image.new("RGB", (10, 10)).save(photo)
            video.write_bytes(b"video")
            images, videos = notify_telegram.split_slides([photo, video])
            self.assertEqual((images, videos), ([photo.resolve()], [video.resolve()]))
            with self.assertRaises(ValueError):
                notify_telegram.split_slides([photo])


class TelegramFlowTests(CollectionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.db = watcher.connect_database(self.root / "watcher.sqlite3")
        message = watcher.TelegramMessage(1, 50, "1", "9", "https://x.com/a/status/123", None, "now")
        self.job_id = watcher.enqueue_request(self.db, message)
        self.sent = []

    def tearDown(self) -> None:
        self.db.close()
        super().tearDown()

    def callback(self, data: str, update_id: int = 2) -> watcher.TelegramCallback:
        return watcher.TelegramCallback(update_id, f"cb{update_id}", 60, "1", "9", data)

    def run_callback(self, data: str, update_id: int = 2) -> None:
        with patch.object(watcher, "answer_callback", side_effect=lambda *a: self.sent.append(("answer", a[-1]))), \
                patch.object(watcher, "edit_text", side_effect=lambda s, c, m, text, reply_markup=None: self.sent.append(("edit", text, reply_markup))), \
                patch.object(watcher, "send_text", side_effect=lambda s, c, text, reply_to_message_id, reply_markup=None: self.sent.append(("send", text, reply_markup)) or [70 + len(self.sent)]):
            telegram_ui.handle_callback(watcher, None, SimpleNamespace(telegram_chat_id="1"), self.db, self.callback(data, update_id))

    def test_save_and_build_collects_the_post_and_offers_the_three_types(self) -> None:
        collection = store.create(self.connection, "Jensen saga")
        self.run_callback(f"collect:{self.job_id}")
        picker = self.sent[-1][2]["inline_keyboard"]
        self.assertEqual(picker[0][0]["callback_data"], f"into:{self.job_id}:{collection.id}")
        self.assertEqual(picker[1][0]["callback_data"], f"newcol:{self.job_id}")
        result = {"collection_id": collection.id, "collection": "Jensen saga", "item_id": 1, "count": 3,
                  "photos": 2, "videos": 1}
        with patch.object(telegram_ui, "run_collect", return_value=result) as collect:
            self.run_callback(f"save:{self.job_id}:{collection.id}:build", update_id=3)
        collect.assert_called_once_with("https://x.com/a/status/123", collection.id)
        text, keyboard = self.sent[-1][1], self.sent[-1][2]
        self.assertIn("Saved to Jensen saga: 3 posts (this one with 2 photos and 1 video)", text)
        self.assertEqual([row[0]["callback_data"] for row in keyboard["inline_keyboard"]],
                         [f"build:{collection.id}:media", f"build:{collection.id}:tweets", f"build:{collection.id}:combo"])
        job = self.db.execute("SELECT * FROM jobs WHERE id = ?", (self.job_id,)).fetchone()
        self.assertEqual(job["status"], "completed")

    def test_build_queues_a_collection_job(self) -> None:
        collection = self.collection()
        self.run_callback(f"build:{collection.id}:combo", update_id=4)
        job = self.db.execute("SELECT * FROM jobs WHERE workflow_type = 'collection'").fetchone()
        self.assertEqual(job["request_text"], f"collection:{collection.id}:combo")
        self.assertEqual(job["status"], "queued")
        self.assertIsNotNone(job["progress_message_id"])

    def test_the_dashboard_lists_collections_and_opens_one_in_place(self) -> None:
        for command in ("/m", "/menu", "/dashboard", "/start", "/collections", "/M@BitsTodayBot"):
            self.assertTrue(telegram_ui.COMMAND.match(command), command)
        self.assertFalse(telegram_ui.COMMAND.match("/meme https://x.com/a/status/1"))
        collection = self.collection()
        other = store.create(self.connection, "Empty one")
        text, keyboard = telegram_ui.list_view()
        self.assertIn("Dashboard", text)
        buttons = [row[0] for row in keyboard["inline_keyboard"]]
        self.assertEqual({button["callback_data"] for button in buttons},
                         {f"dash:col:{collection.id}", f"dash:col:{other.id}", "dash:home"})
        self.assertIn(f"{collection.name} · 4 posts", [button["text"] for button in buttons])

        self.run_callback(f"dash:col:{collection.id}", update_id=6)
        rows = self.sent[-1][2]["inline_keyboard"]
        self.assertEqual(rows[0][0]["callback_data"], f"colview:{collection.id}")
        self.assertEqual([button["callback_data"] for button in rows[1]],
                         [f"build:{collection.id}:{kind}" for kind in slides.TYPES])
        self.assertEqual([button["callback_data"] for button in rows[2]], [f"colarchive:{collection.id}", "dash:home"])

        self.run_callback(f"colarchive:{other.id}", update_id=7)
        self.assertNotIn(f"dash:col:{other.id}",
                         [row[0]["callback_data"] for row in self.sent[-1][2]["inline_keyboard"]])
        self.run_callback(f"dash:col:{other.id}", update_id=8)
        self.assertIn(("answer", "That collection is no longer open."), self.sent)

    def test_a_named_reply_creates_the_collection(self) -> None:
        self.run_callback(f"newcol:{self.job_id}")
        prompt_id = self.db.execute("SELECT message_id FROM collection_prompts WHERE kind = 'name'").fetchone()[0]
        reply = watcher.TelegramMessage(5, 80, "1", "9", "GTA 6 ports", prompt_id, "now")
        with patch.object(watcher, "send_text", side_effect=lambda s, c, text, reply_to_message_id, reply_markup=None: self.sent.append(("send", text, reply_markup)) or [99]):
            self.assertTrue(telegram_ui.handle_reply(watcher, None, SimpleNamespace(telegram_chat_id="1"), self.db, reply))
        self.assertEqual([collection.name for collection in store.recent(self.connection)], ["GTA 6 ports"])
        self.assertIn("save:", self.sent[-1][2]["inline_keyboard"][0][0]["callback_data"])

    def test_collection_jobs_run_the_collection_pipeline(self) -> None:
        config = watcher.Config("token", "1", frozenset(), self.root, self.root / "state", self.root / "db", Path("codex"))
        job = {"id": 9, "workflow_type": "collection", "request_text": "collection:3:media", "turn_count": 1,
               "session_id": None}
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            (Path(kwargs["env"]["BITS_TODAY_JOB_DIR"]) / "collection-state.json").write_text(
                json.dumps({"session_id": "collection-job-9"}), encoding="utf-8")
            return SimpleNamespace(returncode=0)

        with patch.object(watcher.subprocess, "run", side_effect=fake_run):
            result = watcher.invoke_codex(config, job, prompt="ignored", reply_to_message_id=5, resume=False)
        self.assertEqual(result.session_id, "collection-job-9")
        self.assertTrue(commands[0][1].endswith(os.path.join("collections", "pipeline.py")))
        self.assertEqual(commands[0][2:], ["start", "--request", "collection:3:media"])


if __name__ == "__main__":
    unittest.main()
