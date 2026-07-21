import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import notify_telegram


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
            )

        self.assertEqual(result["photo_message_id"], 10)
        self.assertEqual(result["photo_message_ids"], [10])
        self.assertEqual(result["description_message_ids"], [11])
        self.assertEqual(session.post.call_count, 2)
        photo_call, message_call = session.post.call_args_list
        self.assertTrue(photo_call.args[0].endswith("/sendPhoto"))
        self.assertEqual(photo_call.kwargs["data"]["chat_id"], "123456")
        self.assertIn("photo", photo_call.kwargs["files"])
        self.assertTrue(message_call.args[0].endswith("/sendMessage"))
        self.assertIn(
            "Detailed draft description.", message_call.kwargs["data"]["text"]
        )

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


if __name__ == "__main__":
    unittest.main()
