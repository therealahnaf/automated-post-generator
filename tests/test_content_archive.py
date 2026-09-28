import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from tools.news import content_archive as archive
from tools.news import prepare_platform_descriptions as captions
from tools.news import publish_facebook as facebook


class ContentArchiveTests(unittest.TestCase):
    def test_r2_delivery_removes_only_durable_outbox_copies(self):
        receipt = archive.prepare_delivery(self.caption, [self.media])
        response = Mock(ok=True)
        response.json.return_value = {'post': {'id': 'r2-post', 'storage_backend': 'r2'}}
        with patch.object(archive.requests, 'post', return_value=response):
            self.assertEqual(archive.confirm_delivery(receipt, 'social-id')['status'], 'stored')
        self.assertTrue(self.media.exists())
        self.assertTrue(receipt.exists())
        self.assertFalse((receipt.parent / '00.png').exists())
        self.assertEqual(archive.deliver(receipt)['status'], 'already_stored')

    def test_relative_facebook_reel_permalink_is_normalized_on_confirmation(self):
        receipt = archive.prepare_delivery(self.caption, [self.media])
        with patch.object(archive, "deliver", return_value={"status": "pending"}):
            archive.confirm_delivery(receipt, "123", "/reel/123/")
        payload = json.loads(receipt.read_text())
        self.assertEqual(payload["document"]["publication_url"],
                         "https://www.facebook.com/reel/123/")

    def test_retry_repairs_legacy_relative_link_without_republishing(self):
        receipt = archive.prepare_delivery(self.caption, [self.media])
        payload = json.loads(receipt.read_text())
        payload["state"] = "pending"
        payload["document"].update(external_key="facebook:123", publication_url="/reel/123/",
                                    published_at="2026-09-27T00:00:00+00:00")
        archive.atomic_json(receipt, payload)
        response = Mock(ok=True)
        response.json.return_value = {"post": {"id": "stored-reel"}}
        with patch.object(archive.requests, "post", return_value=response) as request:
            self.assertEqual(archive.deliver(receipt)["status"], "stored")
        sent = json.loads(request.call_args.kwargs["data"]["document"])
        self.assertEqual(sent["publication_url"], "https://www.facebook.com/reel/123/")
        self.assertEqual(sent["external_key"], "facebook:123")
        self.assertEqual(sent["published_at"], "2026-09-27T00:00:00+00:00")
        self.assertEqual(archive.deliver(receipt)["status"], "already_stored")

    def test_nested_fetch_photos_keep_order_origin_and_deduplicate(self):
        main = {"local_path": str(self.root / "main.jpg"), "origin": "main"}
        quote = {"local_path": str(self.root / "quote.jpg"), "origin": "quote"}
        thread = {"local_path": str(self.root / "thread.jpg"), "origin": "thread"}
        result = archive.original_photo_assets({
            "downloaded_photos": [main],
            "items": [{"downloaded_photos": [main, quote, thread]}],
        })
        self.assertEqual([p["origin"] for p in result], ["main", "quote", "thread"])
        self.assertEqual(len(result), 3)

    def test_manifest_collects_photos_from_fetch_items(self):
        tweet = self.root / "tweet.json"
        photo = self.root / "photo.jpg"
        tweet.write_text(json.dumps({
            "workflow_type": "news", "requested_urls": ["https://x.com/test/status/123"],
            "items": [{"downloaded_photos": [{"local_path": str(photo), "origin": "main"}]}],
        }))
        result = archive.prepare_manifest(title="Title", english="English copy",
                                          tweet_json=tweet, description_file=self.caption)
        self.assertEqual(result["source_media"][0]["path"], str(photo.resolve()))

    def test_legacy_carousel_finds_only_byte_matching_primary_metadata(self):
        carousel = self.root / "carousel"
        carousel.mkdir()
        primary = carousel / "01-headline.png"
        primary.write_bytes(self.media.read_bytes())
        background = self.root / "raw.png"
        background.write_bytes(b"raw-background")
        self.media.with_suffix(".json").write_text(json.dumps({
            "background_source": "openai-image-api", "background_asset_path": str(background),
        }))
        (self.root / "draft.png").write_bytes(b"not-approved")
        (self.root / "draft.json").write_text(json.dumps({"background_source": str(self.media)}))
        self.assertEqual(archive.background_assets([primary]), [
            {"path": background.resolve(), "asset_type": "generated_background"}])

    def test_saved_bundled_background_is_not_classified_as_generated(self):
        bundled = self.root / "bg-1.png"
        bundled.write_bytes(b"bundled")
        saved = self.root / "saved-background.png"
        saved.write_bytes(b"bundled")
        self.media.with_suffix(".json").write_text(json.dumps({
            "background_source": str(bundled), "background_asset_path": str(saved),
        }))
        self.assertEqual(archive.background_assets([self.media]), [
            {"path": saved.resolve(), "asset_type": "bundled_background"}])

    def test_reel_archives_only_after_confirmed_publishing(self):
        from tools.reels import publish_facebook_reel as reel

        video = self.root / "approved.mp4"
        video.write_bytes(b"fake-mp4-for-mocked-publisher")
        info = {"duration": 6, "width": 1080, "height": 1920}
        with (
            patch.object(reel, "validate_reel", return_value=(video, info)),
            patch.object(reel, "load_config"),
            patch.object(reel, "verify_page", return_value={"id": "1", "name": "Test"}),
            patch.object(
                reel,
                "start_upload",
                return_value=("video-id", "https://example.com/upload"),
            ),
            patch.object(reel, "upload_binary"),
            patch.object(reel, "finish_upload", return_value={}),
            patch.object(
                reel,
                "wait_for_published_video",
                return_value={"permalink_url": "https://example.com/reel"},
            ),
            patch.object(
                archive, "prepare_delivery", return_value=Path("reel-receipt")
            ) as prepare,
            patch.object(
                archive, "archive_published", return_value={"status": "stored"}
            ) as confirmed,
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            command = ["--video", str(video), "--description-file", str(self.caption)]
            self.assertEqual(reel.main(command), 0)
            prepare.assert_not_called()
            confirmed.assert_not_called()
            self.assertEqual(reel.main(command + ["--publish", "--confirm", "yes"]), 0)
            confirmed.assert_called_once_with(
                Path("reel-receipt"), "video-id", "https://example.com/reel"
            )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(
            os.environ,
            {
                "CONTENT_API_URL": "http://127.0.0.1:8001",
                "CONTENT_API_KEY": "a" * 48,
                "CONTENT_OUTBOX_DIR": str(self.root / "outbox"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)
        self.caption = self.root / "facebook-description.txt"
        self.caption.write_text("English copy.\n\n---\n\nবাংলা।", encoding="utf-8")
        self.media = self.root / "approved.png"
        self.media.write_bytes(b"original-media")
        self.document = {
            "archive_key": str(uuid4()),
            "title": "English title",
            "description": "English copy.",
            "workflow_type": "news",
            "sources": [{"label": "Example", "url": "https://example.com"}],
            "caption_sha256": hashlib.sha256(self.caption.read_bytes()).hexdigest(),
        }
        archive.manifest_path(self.caption).write_text(
            json.dumps(self.document), encoding="utf-8"
        )

    def test_outbox_survives_api_failure_and_media_changes_at_original_path(self):
        receipt = archive.prepare_delivery(self.caption, [self.media])
        with self.assertRaisesRegex(ValueError, "confirmed"):
            archive.deliver(receipt)
        self.media.write_bytes(b"changed-after-copy")
        with patch.object(archive.requests, "post", side_effect=ConnectionError):
            result = archive.confirm_delivery(receipt, "social-123")
        self.assertEqual(result["status"], "pending")
        stored = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(stored["document"]["external_key"], "facebook:social-123")
        self.assertEqual(
            (receipt.parent / stored["media"][0]["path"]).read_bytes(),
            b"original-media",
        )
        response = Mock(ok=True)
        response.json.return_value = {"post": {"id": "db-id"}}
        with patch.object(archive.requests, "post", return_value=response) as request:
            self.assertEqual(archive.deliver(receipt)["status"], "stored")
            self.assertEqual(archive.deliver(receipt)["status"], "already_stored")
            self.assertEqual(request.call_count, 1)

    def test_instagram_mapping_is_durable_and_idempotent(self):
        key = archive.instagram_archive_key(self.caption)
        with patch.object(archive.requests, "post", side_effect=ConnectionError):
            pending = archive.record_instagram_publication(
                key, "ig-123", "https://www.instagram.com/p/example/"
            )
        self.assertEqual(pending["status"], "pending")
        receipt = Path(pending["receipt"])
        self.assertEqual(json.loads(receipt.read_text())["media_id"], "ig-123")
        response = Mock(ok=True)
        response.json.return_value = {"status": "stored", "post_id": "web-123"}
        with patch.object(archive.requests, "post", return_value=response) as request:
            self.assertEqual(archive.deliver_instagram_mapping(receipt)["status"], "stored")
            self.assertEqual(archive.record_instagram_publication(
                key, "ig-123", "https://www.instagram.com/p/example/"
            )["status"], "already_stored")
            self.assertEqual(request.call_count, 1)
        with self.assertRaisesRegex(ValueError, "Another Instagram media ID"):
            archive.record_instagram_publication(key, "ig-456", None)

    def test_instagram_mapping_requires_unchanged_caption(self):
        self.assertEqual(archive.instagram_archive_key(self.caption), self.document["archive_key"])
        self.caption.write_text("Changed after approval", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs"):
            archive.instagram_archive_key(self.caption)

    def test_instagram_publication_is_still_reported_if_mapping_checkpoint_fails(self):
        with patch.object(archive, "record_instagram_publication", side_effect=OSError):
            result = archive.archive_instagram_published(
                self.document["archive_key"], "ig-123", None
            )
        self.assertEqual(result, {"status": "confirmation_failed", "media_id": "ig-123"})

    def test_changed_caption_and_missing_english_title_rejected(self):
        self.caption.write_text("Edited caption", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs"):
            archive.prepare_delivery(self.caption, [self.media])
        with patch.dict(os.environ, {"CONTENT_API_URL": ""}):
            self.assertIsNone(archive.prepare_delivery(None, [self.media]))

    def test_original_x_photo_is_copied_to_outbox(self):
        original = self.root / "original-x-photo.jpg"
        original.write_bytes(b"original-x-photo")
        manifest = archive.manifest_path(self.caption)
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["source_media"] = [str(original)]
        manifest.write_text(json.dumps(document), encoding="utf-8")
        receipt = archive.prepare_delivery(self.caption, [self.media])
        stored = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(len(stored["source_media"]), 1)
        self.assertEqual(
            (receipt.parent / stored["source_media"][0]["path"]).read_bytes(),
            b"original-x-photo",
        )

    def test_renderer_background_is_archived_as_a_distinct_asset(self):
        background = self.root / "raw-background.png"
        background.write_bytes(b"background-bytes")
        self.media.with_suffix(".json").write_text(json.dumps({
            "background_source": "openai-image-api",
            "background_asset_path": str(background),
        }), encoding="utf-8")
        receipt = archive.prepare_delivery(self.caption, [self.media])
        stored = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(stored["document"]["assets"][0]["asset_type"],
                         "generated_background")
        self.assertEqual(
            (receipt.parent / stored["asset_media"][0]["path"]).read_bytes(),
            b"background-bytes",
        )

    def test_local_backgrounds_are_deduplicated_from_renderer_metadata(self):
        background = self.root / "bg-1.png"
        background.write_bytes(b"bundled-background")
        (self.root / "post.json").write_text(json.dumps({
            "primary_image": str(self.media.resolve()),
            "background_sources": [str(background), str(background)],
        }), encoding="utf-8")
        receipt = archive.prepare_delivery(self.caption, [self.media])
        stored = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(len(stored["asset_media"]), 1)
        self.assertEqual(stored["document"]["assets"][0]["asset_type"],
                         "bundled_background")

    def test_preparation_keeps_english_for_bangla_first_and_all_sources(self):
        tweet = self.root / "tweet.json"
        tweet.write_text(
            json.dumps(
                {
                    "workflow_type": "informative",
                    "requested_urls": ["https://x.com/test/status/123"],
                    "platform_languages": {
                        "facebook": "bangla",
                        "instagram": "english",
                    },
                }
            )
        )
        self.caption.with_suffix(".sources.json").write_text(
            json.dumps({"urls": ["https://example.com/research"]})
        )
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(
                captions.main(
                    [
                        "--description-file",
                        str(self.caption),
                        "--tweet-json",
                        str(tweet),
                        "--english-title",
                        "English hook",
                        "--output-dir",
                        str(self.root / "captions"),
                    ]
                ),
                0,
            )
        manifest = json.loads(
            (self.root / "captions/facebook-description.website.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["description"], "English copy.")
        self.assertEqual(manifest["workflow_type"], "informative")
        self.assertEqual(len(manifest["sources"]), 2)
        instagram_manifest = json.loads(
            (self.root / "captions/instagram-description.website.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["archive_key"], instagram_manifest["archive_key"])

    def test_dry_run_and_host_only_never_prepare_archive(self):
        common = ["--image", str(self.media), "--message-file", str(self.caption)]
        with (
            patch.object(facebook, "validate_image_paths", return_value=[self.media]),
            patch.object(facebook, "load_config"),
            patch.object(
                facebook, "verify_page", return_value={"id": "1", "name": "Test"}
            ),
            patch.object(
                facebook,
                "host_unpublished_images",
                return_value=(["photo"], [{}], ["https://example.com/photo"]),
            ),
            patch.object(facebook, "prepare_delivery") as prepare,
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            self.assertEqual(facebook.main(common), 0)
            self.assertEqual(
                facebook.main(
                    common + ["--host-only", "--publish", "--confirm", "yes"]
                ),
                0,
            )
            prepare.assert_not_called()

    def test_publication_archives_before_optional_photo_details(self):
        with (
            patch.object(facebook, "validate_image_paths", return_value=[self.media]),
            patch.object(facebook, "load_config"),
            patch.object(
                facebook, "verify_page", return_value={"id": "1", "name": "Test"}
            ),
            patch.object(facebook, "prepare_delivery", return_value=Path("receipt")),
            patch.object(
                facebook,
                "publish_photo",
                return_value={"id": "photo", "post_id": "published"},
            ),
            patch.object(
                facebook, "archive_published", return_value={"status": "pending"}
            ) as confirm,
            patch.object(
                facebook,
                "get_photo_details",
                side_effect=RuntimeError("details unavailable"),
            ),
            patch("sys.stderr", new_callable=io.StringIO),
        ):
            facebook.main(
                [
                    "--image",
                    str(self.media),
                    "--message-file",
                    str(self.caption),
                    "--publish",
                    "--confirm",
                    "yes",
                ]
            )
            confirm.assert_called_once_with(Path("receipt"), "published")


if __name__ == "__main__":
    unittest.main()
