"""API integration tests in a temporary schema inside the configured database."""

import io
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from PIL import Image
from psycopg import sql
from psycopg.rows import dict_row

from content_api.app import app
from content_api import storage
from content_api.config import database_url


def app_connect_for_test(schema):
    conn = psycopg.connect(database_url())
    conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
    return conn


class ContentApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = "content_test_" + uuid4().hex
        with psycopg.connect(database_url()) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
            conn.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(cls.schema))
            )
            conn.execute(Path("content_api/schema.sql").read_text())

        def test_connect():
            conn = psycopg.connect(database_url(), row_factory=dict_row)
            conn.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(cls.schema))
            )
            return conn

        cls.connection_patch = patch("content_api.app.connect", test_connect)
        cls.key_patch = patch("content_api.app.api_key", return_value="a" * 48)
        cls.connection_patch.start()
        cls.key_patch.start()
        def legacy_save(conn, digest, mime, data):
            conn.execute('INSERT INTO content_media(sha256,mime_type,data,byte_size) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                         (digest, mime, data, len(data)))
        cls.save_patch = patch.object(storage, 'save', side_effect=legacy_save)
        cls.save_patch.start()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.connection_patch.stop()
        cls.key_patch.stop()
        cls.save_patch.stop()
        # Only the randomly named schema created by this test suite is removed.
        with psycopg.connect(database_url()) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema))
            )

    def payload(self, **overrides):
        return {
            "external_key": uuid4().hex,
            "workflow_type": "news",
            "title": "An English headline",
            "description": "An English description.",
            "sources": [{"label": "Source", "url": "https://example.com/story"}],
            "published_at": datetime.now(timezone.utc).isoformat(),
            **overrides,
        }

    def test_r2_media_redirects_without_loading_database_bytes(self):
        image = io.BytesIO()
        Image.new('RGB', (9, 9), '#123456').save(image, format='PNG')
        post = self.upload(self.payload(), image.getvalue()).json()['post']
        digest = post['media'][0]['sha256']
        with app_connect_for_test(self.schema) as conn:
            conn.execute('UPDATE content_media SET object_key=%s,data=NULL WHERE sha256=%s', ('media/test.png', digest))
        with patch.object(storage, 'url', return_value='https://r2.example/media/test.png'):
            response = self.client.get('/api/media/' + digest, follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['location'], 'https://r2.example/media/test.png')

    def upload(self, payload, data=None, headers=None):
        if data is None:
            image = io.BytesIO()
            Image.new("RGB", (10, 10), "#ff5757").save(image, format="PNG")
            data = image.getvalue()
        return self.client.post(
            "/api/posts",
            data={"document": json.dumps(payload)},
            files=[("files", ("photo.png", data, "image/png"))],
            headers=headers
            if headers is not None
            else {"Authorization": "Bearer " + "a" * 48},
        )

    def test_authentication_and_validation(self):
        self.assertEqual(self.upload(self.payload(), headers={}).status_code, 401)
        self.assertEqual(self.upload(self.payload(title="বাংলা")).status_code, 422)
        self.assertEqual(
            self.upload(self.payload(workflow_type="invalid")).status_code, 422
        )
        self.assertEqual(
            self.upload(self.payload(), b"<script>bad</script>").status_code, 422
        )

    def test_all_workflows_are_supported(self):
        for kind in ("news", "model", "product", "informative", "reel"):
            with self.subTest(kind=kind):
                response = self.upload(self.payload(workflow_type=kind))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["post"]["workflow_type"], kind)

    def test_idempotency_conflict_and_media(self):
        payload = self.payload()
        first = self.upload(payload).json()
        second = self.upload(payload).json()
        self.assertEqual(first["post"]["id"], second["post"]["id"])
        self.assertEqual(second["status"], "already_stored")
        self.assertEqual(
            self.upload({**payload, "title": "Different"}).status_code, 409
        )
        media = self.client.get(first["post"]["media"][0]["url"])
        self.assertEqual(media.status_code, 200)
        self.assertTrue(media.content.startswith(b"\x89PNG"))
        detail = self.client.get("/api/posts/" + first["post"]["id"]).json()
        self.assertEqual(detail["sources"], payload["sources"])
        self.assertNotIn("data", detail["media"][0])

    def test_video_range_and_type_filter(self):
        data = b"\x00\x00\x00\x18ftypmp42" + b"0" * 100
        response = self.upload(self.payload(workflow_type="reel"), data)
        self.assertEqual(response.status_code, 200)
        media = response.json()["post"]["media"][0]
        self.assertEqual(media["kind"], "video")
        part = self.client.get(media["url"], headers={"Range": "bytes=4-11"})
        self.assertEqual(part.status_code, 206)
        self.assertEqual(part.content, data[4:12])
        self.assertEqual(
            self.client.get(media["url"], headers={"Range": "bytes=999-"}).status_code,
            416,
        )
        self.assertEqual(
            self.client.get(media["url"], headers={"Range": "bytes=-4"}).content,
            data[-4:],
        )
        result = self.client.get("/api/posts?workflow_type=reel&limit=1").json()
        self.assertEqual(result["items"][0]["workflow_type"], "reel")

    def test_media_order_and_transaction_rollback(self):
        first = b"\x00\x00\x00\x18ftypmp42" + b"first"
        second = b"\x00\x00\x00\x18ftypmp42" + b"second"
        payload = self.payload(workflow_type="reel")
        response = self.client.post(
            "/api/posts",
            headers={"Authorization": "Bearer " + "a" * 48},
            data={"document": json.dumps(payload)},
            files=[("files", ("a.mp4", first)), ("files", ("b.mp4", second))],
        )
        media = response.json()["post"]["media"]
        self.assertEqual(
            [self.client.get(item["url"]).content for item in media], [first, second]
        )
        self.assertEqual(self.client.get("/api/posts?limit=0").status_code, 422)

    def test_original_x_photos_are_separate_and_ordered(self):
        def png(color):
            output = io.BytesIO()
            Image.new("RGB", (10, 10), color).save(output, format="PNG")
            return output.getvalue()

        payload = self.payload()
        first, second = png("#ff5757"), png("#c2ffe1")
        request_files = [
            ("files", ("social.png", png("#212121"), "image/png")),
            ("source_files", ("x-1.png", first, "image/png")),
            ("source_files", ("x-2.png", second, "image/png")),
        ]
        response = self.client.post(
            "/api/posts", data={"document": json.dumps(payload)},
            files=request_files, headers={"Authorization": "Bearer " + "a" * 48},
        )
        self.assertEqual(response.status_code, 200)
        post = response.json()["post"]
        self.assertEqual(len(post["media"]), 1)
        self.assertEqual(
            [self.client.get(item["url"]).content for item in post["source_media"]],
            [first, second],
        )
        retry = self.client.post(
            "/api/posts", data={"document": json.dumps(payload)},
            files=request_files, headers={"Authorization": "Bearer " + "a" * 48},
        )
        self.assertEqual(retry.json()["status"], "already_stored")

    def test_app_assets_can_be_attached_without_republishing(self):
        image = io.BytesIO()
        Image.new("RGB", (10, 10), "#c2ffe1").save(image, format="PNG")
        original = image.getvalue()
        post = self.upload(self.payload()).json()["post"]
        document = {"assets": [
            {"asset_type": "x_photo", "origin": "thread",
             "source_url": "https://pbs.twimg.com/media/example"},
            {"asset_type": "generated_background"},
        ]}
        files = [("files", ("x.png", original, "image/png")),
                 ("files", ("background.png", original, "image/png"))]
        url = f"/api/posts/{post['id']}/assets"
        headers = {"Authorization": "Bearer " + "a" * 48}
        response = self.client.post(url, data={"document": json.dumps(document)},
                                    files=files, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["added"], 2)
        updated = self.client.get(f"/api/posts/{post['id']}").json()
        self.assertEqual([item["asset_type"] for item in updated["assets"]],
                         ["x_photo", "generated_background"])
        self.assertEqual(updated["assets"][0]["origin"], "thread")
        self.assertEqual(len(updated["source_media"]), 1)
        repeat = self.client.post(url, data={"document": json.dumps(document)},
                                  files=files, headers=headers)
        self.assertEqual(repeat.json()["status"], "already_stored")


if __name__ == "__main__":
    unittest.main()
