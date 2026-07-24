import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from tools.news import notify_telegram


class NotifyTelegramTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = notify_telegram.TelegramConfig(
            bot_token="secret-telegram-token",
            chat_id="123456",
        )

    def test_split_message_prefers_paragraph_boundaries(self) -> None:
        text = "First paragraph.\n\nSecond paragraph is longer."
        chunks = notify_telegram.split_message(text, limit=20)

        self.assertEqual(chunks, ["First paragraph.", "Second paragraph is", "longer."])
        self.assertTrue(all(len(chunk) <= 20 for chunk in chunks))

    def test_split_message_hard_splits_long_word(self) -> None:
        self.assertEqual(
            notify_telegram.split_message("abcdefghijk", limit=5),
            ["abcde", "fghij", "k"],
        )

    def test_parse_error_does_not_include_bot_token(self) -> None:
        response = Mock()
        response.ok = False
        response.status_code = 400
        response.json.return_value = {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: chat not found",
        }

        with self.assertRaisesRegex(RuntimeError, "chat not found") as error:
            notify_telegram.parse_telegram_response(response)
        self.assertNotIn("secret-telegram-token", str(error.exception))

    def test_send_review_package_sends_photo_then_description(self) -> None:
        photo_response = Mock()
        photo_response.ok = True
        photo_response.status_code = 200
        photo_response.json.return_value = {
            "ok": True,
            "result": {"message_id": 10},
        }
        message_response = Mock()
        message_response.ok = True
        message_response.status_code = 200
        message_response.json.return_value = {
            "ok": True,
            "result": {"message_id": 11},
        }
        session = Mock()
        session.post.side_effect = [photo_response, message_response]

        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "post.png"
            image.write_bytes(b"test-image")
            result = notify_telegram.send_review_package(
                session,
                self.config,
                image=image,
                description="Detailed draft description.",
                stage="preview",
                reply_to_message_id=7,
            )

        self.assertEqual(result["photo_message_id"], 10)
        self.assertEqual(result["photo_message_ids"], [10])
        self.assertEqual(result["description_message_ids"], [11])
        self.assertEqual(session.post.call_count, 2)
        photo_call, message_call = session.post.call_args_list
        self.assertTrue(photo_call.args[0].endswith("/sendPhoto"))
        self.assertEqual(photo_call.kwargs["data"]["chat_id"], "123456")
        self.assertEqual(
            json.loads(photo_call.kwargs["data"]["reply_parameters"])["message_id"],
            7,
        )
        self.assertIn("photo", photo_call.kwargs["files"])
        self.assertTrue(message_call.args[0].endswith("/sendMessage"))
        self.assertIn(
            "Detailed draft description.", message_call.kwargs["data"]["text"]
        )
        self.assertEqual(result["reply_to_message_id"], 7)

    def test_send_review_package_sends_main_then_secondary_images(self) -> None:
        responses = []
        for message_id in (20, 21, 22):
            response = Mock()
            response.ok = True
            response.status_code = 200
            response.json.return_value = {
                "ok": True,
                "result": {"message_id": message_id},
            }
            responses.append(response)
        session = Mock()
        session.post.side_effect = responses

        with tempfile.TemporaryDirectory() as temp_dir:
            main_image = Path(temp_dir) / "main.png"
            source_image = Path(temp_dir) / "source.jpg"
            main_image.write_bytes(b"main-image")
            source_image.write_bytes(b"source-image")
            result = notify_telegram.send_review_package(
                session,
                self.config,
                image=main_image,
                secondary_images=[source_image],
                description="Ordered package.",
                stage="preview",
            )

        self.assertEqual(result["photo_message_ids"], [20, 21])
        self.assertEqual(result["description_message_ids"], [22])
        main_call, secondary_call, description_call = session.post.call_args_list
        self.assertEqual(main_call.kwargs["files"]["photo"][0], "main.png")
        self.assertIn("MAIN IMAGE", main_call.kwargs["data"]["caption"])
        self.assertEqual(secondary_call.kwargs["files"]["photo"][0], "source.jpg")
        self.assertIn("SOURCE IMAGE 1", secondary_call.kwargs["data"]["caption"])
        self.assertTrue(description_call.args[0].endswith("/sendMessage"))

    def test_writes_atomic_preview_receipt_with_package_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "main.png"
            receipt = root / "state" / "receipt.json"
            image.write_bytes(b"main-image")
            notify_telegram.write_preview_receipt(
                receipt,
                job_id="42",
                reply_to_message_id=9,
                images=[image],
                description="Preview copy",
                telegram_result={
                    "photo_message_ids": [100],
                    "description_message_ids": [101],
                },
            )
            payload = json.loads(receipt.read_text(encoding="utf-8"))

        self.assertEqual(payload["job_id"], "42")
        self.assertEqual(payload["reply_to_message_id"], 9)
        self.assertEqual(payload["photo_message_ids"], [100])
        self.assertEqual(len(payload["image_sha256s"][0]), 64)
        self.assertEqual(len(payload["description_sha256"]), 64)

    def test_send_video_review_package_sends_video_then_description(self) -> None:
        video_response = Mock()
        video_response.ok = True
        video_response.status_code = 200
        video_response.json.return_value = {
            "ok": True,
            "result": {"message_id": 30},
        }
        message_response = Mock()
        message_response.ok = True
        message_response.status_code = 200
        message_response.json.return_value = {
            "ok": True,
            "result": {"message_id": 31},
        }
        session = Mock()
        session.post.side_effect = [video_response, message_response]
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "reel.mp4"
            video.write_bytes(b"test-video")
            result = notify_telegram.send_video_review_package(
                session,
                self.config,
                video=video,
                description="Reel description.",
                stage="preview",
                reply_to_message_id=12,
            )
        self.assertEqual(result["video_message_ids"], [30])
        self.assertEqual(result["description_message_ids"], [31])
        self.assertTrue(session.post.call_args_list[0].args[0].endswith("/sendVideo"))
        self.assertIn("video", session.post.call_args_list[0].kwargs["files"])


if __name__ == "__main__":
    unittest.main()
