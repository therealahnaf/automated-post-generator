import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from tools.news import generate_carousel_copy


class NewsGenerateCarouselCopyTests(unittest.TestCase):
    def make_fixture(self, root: Path, photo_count: int, feature_index: int | None):
        photos = []
        paths = []
        for index in range(photo_count):
            path = root / f"photo-{index + 1}.png"
            Image.new("RGB", (800, 600), (index * 20, 60, 120)).save(path)
            paths.append(path.resolve())
            photos.append({"position": index + 1, "local_path": str(path.resolve())})
        tweet = root / "tweet.json"
        tweet.write_text(
            json.dumps({"items": [{"id": "123", "downloaded_photos": photos}]}),
            encoding="utf-8",
        )
        metadata = root / "post.json"
        metadata.write_text(
            json.dumps(
                {
                    "feature_image_source": (
                        str(paths[feature_index]) if feature_index is not None else None
                    )
                }
            ),
            encoding="utf-8",
        )
        return tweet, metadata, paths

    def test_excludes_only_the_photo_embedded_in_primary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tweet, metadata, paths = self.make_fixture(root, 3, 0)
            plan = generate_carousel_copy.build_carousel_plan(tweet, metadata)
        self.assertEqual(plan.mode, "media")
        self.assertEqual(plan.secondary_source_images, [str(paths[1]), str(paths[2])])
        self.assertEqual(generate_carousel_copy.required_description_count(plan), 2)

    def test_ineligible_first_photo_remains_first_detail_card(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tweet, metadata, paths = self.make_fixture(root, 2, None)
            plan = generate_carousel_copy.build_carousel_plan(tweet, metadata)
        self.assertEqual(plan.secondary_source_images, [str(path) for path in paths])

    def test_no_images_creates_exactly_one_fallback_detail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tweet, metadata, _ = self.make_fixture(root, 0, None)
            plan = generate_carousel_copy.build_carousel_plan(tweet, metadata)
        self.assertEqual(plan.mode, "fallback")
        self.assertEqual(generate_carousel_copy.required_description_count(plan), 1)

    def test_one_embedded_photo_produces_no_secondary_card(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tweet, metadata, _ = self.make_fixture(root, 1, 0)
            plan = generate_carousel_copy.build_carousel_plan(tweet, metadata)
        self.assertEqual(plan.mode, "none")
        self.assertEqual(generate_carousel_copy.required_description_count(plan), 0)

    def test_generation_uses_fixed_model_and_exact_count(self) -> None:
        calls = []
        client = SimpleNamespace(
            responses=SimpleNamespace(
                create=lambda **kwargs: (
                    calls.append(kwargs)
                    or SimpleNamespace(output_text='["First detail", "Second detail"]')
                )
            )
        )
        result = generate_carousel_copy.generate_short_descriptions(
            client,
            "Company launches its new platform",
            "The launch includes lower pricing and wider availability.",
            "The platform cuts costs and will reach five new markets.",
            2,
        )
        self.assertEqual(result, ["First detail", "Second detail"])
        self.assertEqual(calls[0]["model"], "gpt-5.6-luna")
        prompt = calls[0]["input"][1]["content"]
        self.assertIn("exactly 2", prompt)
        self.assertIn("Company launches its new platform", prompt)
        self.assertIn("lower pricing and wider availability", prompt)
        self.assertIn("cuts costs and will reach five new markets", prompt)
        self.assertIn("do not repeat or closely", prompt)

    def test_reads_english_headline_from_post_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "post.json"
            metadata.write_text(
                json.dumps(
                    {
                        "title": "বাংলা শিরোনাম",
                        "english_title": "The original English headline",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            headline = generate_carousel_copy.read_english_headline(metadata)

        self.assertEqual(headline, "The original English headline")


if __name__ == "__main__":
    unittest.main()
