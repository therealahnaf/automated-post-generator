import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from tools.instagram import publish_with_music


def instrumental_track(title: str = "Signal (Instrumental)") -> dict:
    return {
        "audio_asset_id": "asset-1",
        "audio_cluster_id": "cluster-1",
        "music_canonical_id": "music-1",
        "title": title,
        "display_artist": "Example Artist",
        "duration_in_ms": 120000,
        "highlight_start_times_in_ms": [9000],
        "is_explicit": False,
    }


class FakeClient:
    def __init__(self) -> None:
        self.photo_calls = []
        self.album_calls = []
        self.dumped = None
        self.loaded = []
        self.login_calls = []
        self.cookie_dict = {}

    def load_settings(self, path: Path) -> dict:
        self.loaded.append(path)
        return {"cookies": {"sessionid": "1:" + "x" * 40}}

    def login_by_sessionid(self, session_id: str) -> bool:
        self.login_calls.append(session_id)
        self.cookie_dict = {"sessionid": session_id}
        return True

    def account_info(self) -> SimpleNamespace:
        return SimpleNamespace(username="bits_t0day")

    def dump_settings(self, path: Path) -> None:
        self.dumped = path
        path.write_text("{}", encoding="utf-8")

    def music_in_feed_audio_browser(self) -> dict:
        return {
            "browse_session_id": "browse-session",
            "alacorn_session_id": "alacorn-session",
            "items": [
                {
                    "playlist": {
                        "preview_items": [
                            {"track": instrumental_track()},
                        ]
                    }
                }
            ]
        }

    def photo_upload_with_music(self, *args, **kwargs) -> SimpleNamespace:
        self.photo_calls.append((args, kwargs))
        return SimpleNamespace(pk="1", id="1_2", code="photo")

    def album_upload_with_music(self, *args, **kwargs) -> SimpleNamespace:
        self.album_calls.append((args, kwargs))
        return SimpleNamespace(pk="2", id="2_2", code="album")


class InstagramPublishWithMusicTests(unittest.TestCase):
    def test_publish_requires_exact_confirmation(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "--confirm yes"):
            publish_with_music.require_publish_confirmation(True, "YES")
        publish_with_music.require_publish_confirmation(True, "yes")
        publish_with_music.require_publish_confirmation(False, None)

    def test_single_image_and_carousel_require_matching_aspect_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            square = root / "square.png"
            Image.new("RGB", (1080, 1350)).save(first)
            Image.new("RGB", (1080, 1350)).save(second)
            Image.new("RGB", (1080, 1080)).save(square)

            paths, _ = publish_with_music.validate_image_paths([first])
            self.assertEqual(paths, [first.resolve()])
            publish_with_music.validate_image_paths([first, second])
            with self.assertRaisesRegex(ValueError, "one aspect ratio"):
                publish_with_music.validate_image_paths([first, square])

    def test_music_image_urls_are_limited_to_facebook_https_cdn(self) -> None:
        accepted = "https://scontent.fdac24-2.fna.fbcdn.net/post.png"
        self.assertEqual(
            publish_with_music.validate_hosted_image_url(accepted),
            accepted,
        )
        for rejected in (
            "http://scontent.fdac24-2.fna.fbcdn.net/post.png",
            "https://example.com/post.png",
            "https://fbcdn.net.attacker.example/post.png",
        ):
            with self.subTest(rejected=rejected):
                with self.assertRaises(ValueError):
                    publish_with_music.validate_hosted_image_url(rejected)

    def test_browser_tracks_are_filtered_to_non_explicit_instrumentals(self) -> None:
        instrumental = instrumental_track()
        vocal = instrumental_track("Signal")
        explicit = instrumental_track("Dark Signal (Instrumental)")
        explicit["is_explicit"] = True
        payload = {
            "items": [
                {
                    "playlist": {
                        "preview_items": [
                            {"track": vocal},
                            {"track": explicit},
                            {"track": instrumental},
                        ]
                    }
                }
            ]
        }
        tracks = publish_with_music.flatten_browser_tracks(payload)
        with mock.patch.object(
            publish_with_music.secrets,
            "choice",
            return_value=instrumental,
        ):
            selected, eligible_count = (
                publish_with_music.select_random_instrumental(tracks)
            )

        self.assertIs(selected, instrumental)
        self.assertEqual(eligible_count, 1)

    def test_deterministic_selection_is_stable_across_catalog_order(self) -> None:
        first = instrumental_track("Alpha (Instrumental)")
        first["audio_asset_id"] = "asset-a"
        second = instrumental_track("Beta (Instrumental)")
        second["audio_asset_id"] = "asset-b"

        selected, count = publish_with_music.select_deterministic_instrumental(
            [first, second],
            "post-fingerprint",
        )
        reordered, reordered_count = (
            publish_with_music.select_deterministic_instrumental(
                [second, first],
                "post-fingerprint",
            )
        )

        self.assertEqual(selected["audio_asset_id"], reordered["audio_asset_id"])
        self.assertEqual((count, reordered_count), (2, 2))

    def test_music_selection_round_trips_without_browser_session_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "music.json"
            client = SimpleNamespace(
                music_in_feed_audio_browser=lambda: {
                    "alacorn_session_id": "temporary-browser-token",
                    "items": [
                        {
                            "playlist": {
                                "preview_items": [
                                    {"track": instrumental_track()}
                                ]
                            }
                        }
                    ],
                }
            )
            selection = publish_with_music.select_and_save_instrumental(
                client,
                path,
            )
            loaded = publish_with_music.load_music_selection(path)
            serialized = path.read_text(encoding="utf-8")

        self.assertEqual(loaded["track"], selection["track"])
        self.assertNotIn("temporary-browser-token", serialized)

    def test_environment_session_login_verifies_expected_account(self) -> None:
        client = FakeClient()
        account = publish_with_music.login_client(
            client,
            publish_with_music.PrivateInstagramConfig(
                expected_username="bits_t0day",
                session_id="1:" + "x" * 40,
            ),
        )

        self.assertEqual(account.username, "bits_t0day")

    def test_matching_loaded_session_is_validated_without_reimport(self) -> None:
        session_id = "1:" + "x" * 40
        client = FakeClient()
        client.cookie_dict = {"sessionid": session_id}

        account = publish_with_music.login_client(
            client,
            publish_with_music.PrivateInstagramConfig(
                expected_username="bits_t0day",
                session_id=session_id,
            ),
        )

        self.assertEqual(account.username, "bits_t0day")
        self.assertEqual(client.login_calls, [])

    def test_client_loads_saved_full_session_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session.json"
            session.write_text("{}", encoding="utf-8")
            client = FakeClient()

            with mock.patch.dict(
                "sys.modules",
                {"instagrapi": SimpleNamespace(Client=lambda: client)},
            ):
                result = publish_with_music.make_client(session)

        self.assertIs(result, client)
        self.assertEqual(client.loaded, [session])

    def test_config_reads_session_id_from_environment(self) -> None:
        session_id = "1:" + "x" * 40
        with mock.patch.dict(
            "os.environ",
            {
                "INSTAGRAM_PRIVATE_USERNAME": "bits_t0day",
                "INSTAGRAM_PRIVATE_SESSIONID": session_id,
            },
        ):
            config = publish_with_music.load_config()

        self.assertEqual(config.expected_username, "bits_t0day")
        self.assertEqual(config.session_id, session_id)

    def test_rotated_cookie_updates_authorization_header(self) -> None:
        client = SimpleNamespace(
            cookie_dict={"sessionid": "new-session"},
            sessionid="old-session",
            authorization_data={"sessionid": "old-session"},
            authorization="Bearer rotated-authorization",
            private=SimpleNamespace(headers={}),
        )

        session_id = publish_with_music.synchronize_client_authorization(client)

        self.assertEqual(session_id, "new-session")
        self.assertEqual(client.authorization_data["sessionid"], "new-session")
        self.assertEqual(
            client.private.headers["Authorization"],
            "Bearer rotated-authorization",
        )

    def test_rotated_cookie_is_persisted_without_duplicate_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "OPENAI_API_KEY=test\nINSTAGRAM_PRIVATE_SESSIONID=old\n",
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {}, clear=False):
                changed = publish_with_music.persist_private_session_id(
                    "new-session",
                    env_path=env_path,
                )
            content = env_path.read_text(encoding="utf-8")

        self.assertTrue(changed)
        self.assertEqual(content.count("INSTAGRAM_PRIVATE_SESSIONID="), 1)
        self.assertIn("INSTAGRAM_PRIVATE_SESSIONID=new-session", content)

    def test_full_session_settings_are_persisted_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path = root / ".env"
            session_file = root / "private" / "session.json"
            client = FakeClient()
            client.cookie_dict = {"sessionid": "new-session"}

            with mock.patch.dict(os.environ, {}, clear=False):
                session_id = publish_with_music.synchronize_and_persist_session(
                    client,
                    session_file=session_file,
                    env_path=env_path,
                )

            self.assertEqual(session_id, "new-session")
            self.assertTrue(session_file.is_file())
            self.assertEqual(client.dumped.parent, session_file.parent)
            self.assertTrue(client.dumped.name.startswith(".session.json."))
            self.assertTrue(client.dumped.name.endswith(".tmp"))
            self.assertIn(
                "INSTAGRAM_PRIVATE_SESSIONID=new-session",
                env_path.read_text(encoding="utf-8"),
            )

    def test_upload_dispatches_single_photo_and_carousel_methods(self) -> None:
        client = FakeClient()
        track = instrumental_track()
        first = Path("first.png")
        second = Path("second.png")

        publish_with_music.upload_with_music(
            client,
            [first],
            "Caption",
            track,
            overlap_duration_ms=30000,
        )
        publish_with_music.upload_with_music(
            client,
            [first, second],
            "Caption",
            track,
            overlap_duration_ms=30000,
        )

        self.assertEqual(len(client.photo_calls), 1)
        self.assertEqual(len(client.album_calls), 1)
        self.assertEqual(
            client.album_calls[0][1]["audio_asset_start_time"],
            9000,
        )

    def test_upload_forwards_music_browser_session_identifiers(self) -> None:
        client = FakeClient()
        publish_with_music.upload_with_music(
            client,
            [Path("first.png"), Path("second.png")],
            "Caption",
            instrumental_track(),
            overlap_duration_ms=30000,
            browse_session_id="browse-session",
            alacorn_session_id="alacorn-session",
        )

        kwargs = client.album_calls[0][1]
        self.assertEqual(kwargs["browse_session_id"], "browse-session")
        self.assertEqual(kwargs["alacorn_session_id"], "alacorn-session")

    def test_url_publisher_selects_once_and_reuses_published_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = root / "receipt.json"
            image = root / "post.png"
            Image.new("RGB", (1080, 1350)).save(image)
            config = publish_with_music.PrivateInstagramConfig(
                expected_username="bits_t0day",
                session_id="1:" + "x" * 40,
            )
            client = FakeClient()
            with (
                mock.patch.object(
                    publish_with_music,
                    "load_config",
                    return_value=config,
                ),
                mock.patch.object(
                    publish_with_music,
                    "download_image_urls",
                    return_value=[image],
                ),
                mock.patch.object(
                    publish_with_music,
                    "synchronize_and_persist_session",
                    return_value="",
                ),
            ):
                first = publish_with_music.publish_urls_with_music(
                    ["https://scontent.example.fbcdn.net/post.png"],
                    "Approved caption",
                    publish=True,
                    confirmation="yes",
                    receipt_file=receipt,
                    client=client,
                )
                second = publish_with_music.publish_urls_with_music(
                    ["https://scontent.example.fbcdn.net/post.png"],
                    "Approved caption",
                    publish=True,
                    confirmation="yes",
                    receipt_file=receipt,
                    client=FakeClient(),
                )

        self.assertEqual(first, second)
        self.assertEqual(first["publish_method"], "music")
        self.assertEqual(first["status"], "published")
        self.assertEqual(len(client.photo_calls), 1)
        self.assertEqual(
            client.photo_calls[0][1]["alacorn_session_id"],
            "alacorn-session",
        )

    def test_failed_url_upload_still_persists_session_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = root / "receipt.json"
            image = root / "post.png"
            session = root / "session.json"
            Image.new("RGB", (1080, 1350)).save(image)
            config = publish_with_music.PrivateInstagramConfig(
                expected_username="bits_t0day",
                session_id="1:" + "x" * 40,
                session_file=session,
            )
            client = FakeClient()
            with (
                mock.patch.object(
                    publish_with_music,
                    "load_config",
                    return_value=config,
                ),
                mock.patch.object(
                    publish_with_music,
                    "download_image_urls",
                    return_value=[image],
                ),
                mock.patch.object(
                    publish_with_music,
                    "upload_with_music",
                    side_effect=RuntimeError("configure failed"),
                ),
                mock.patch.object(
                    publish_with_music,
                    "synchronize_and_persist_session",
                    return_value="",
                ) as persist,
            ):
                with self.assertRaisesRegex(RuntimeError, "configure failed"):
                    publish_with_music.publish_urls_with_music(
                        ["https://scontent.example.fbcdn.net/post.png"],
                        "Approved caption",
                        publish=True,
                        confirmation="yes",
                        receipt_file=receipt,
                        client=client,
                    )

        self.assertEqual(persist.call_count, 3)

    def test_dry_run_validates_without_logging_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "post.png"
            caption = root / "caption.txt"
            music = root / "music.json"
            Image.new("RGB", (1080, 1350)).save(image)
            caption.write_text("Approved caption", encoding="utf-8")
            music.write_text(
                json.dumps({"track": instrumental_track()}),
                encoding="utf-8",
            )

            with (
                mock.patch.object(
                    publish_with_music,
                    "make_client",
                    side_effect=AssertionError("must not log in"),
                ),
                mock.patch("builtins.print") as mocked_print,
            ):
                result = publish_with_music.main(
                    [
                        "--image",
                        str(image),
                        "--caption-file",
                        str(caption),
                        "--music-selection",
                        str(music),
                    ]
                )

        self.assertEqual(result, 0)
        payload = json.loads(mocked_print.call_args.args[0])
        self.assertEqual(payload["status"], "validated_not_published")


if __name__ == "__main__":
    unittest.main()
