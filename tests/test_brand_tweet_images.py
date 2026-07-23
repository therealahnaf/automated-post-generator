import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from tools.news import brand_tweet_images


class BrandTweetImagesTests(unittest.TestCase):
    def test_centers_entire_source_in_fixed_frame_without_upscaling(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "tweet.jpg"
            logo_path = root / "logo.png"
            output_path = root / "tweet-branded.jpg"
            Image.new("RGB", (400, 240), "white").save(source_path)
            logo = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
            ImageDraw.Draw(logo).rectangle((10, 10, 70, 70), fill="#FF5757")
            logo.save(logo_path)

            metadata = brand_tweet_images.brand_tweet_image(
                source_path,
                output_path,
                logo_path=logo_path,
                border_width=20,
            )

            with Image.open(output_path) as result:
                self.assertEqual(result.size, (1080, 1350))
                border_pixel = result.convert("RGB").getpixel((2, 2))
                source_pixel = result.convert("RGB").getpixel((345, 560))
                corner_crop = result.convert("RGB").crop((880, 1150, 1078, 1348))
                colors = corner_crop.getcolors(maxcolors=corner_crop.width * corner_crop.height)

            self.assertTrue(all(abs(actual - expected) <= 3 for actual, expected in zip(border_pixel, (33, 33, 33))))
            self.assertTrue(all(channel >= 245 for channel in source_pixel))
            self.assertTrue(any(red > 220 and green < 130 for _, (red, green, _) in colors or []))
            self.assertEqual(metadata["source_size"], [400, 240])
            self.assertEqual(metadata["rendered_size"], [400, 240])
            self.assertEqual(metadata["source_box"], [340, 555, 740, 795])
            self.assertEqual(metadata["aspect_ratio"], "4:5")
            self.assertEqual(metadata["border_color"], "#212121")

    def test_scales_large_portrait_down_without_cropping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "portrait.png"
            logo_path = root / "logo.png"
            output_path = root / "portrait-branded.png"
            Image.new("RGB", (500, 2000), "white").save(source_path)
            logo = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
            ImageDraw.Draw(logo).rectangle((10, 10, 70, 70), fill="#FF5757")
            logo.save(logo_path)

            metadata = brand_tweet_images.brand_tweet_image(
                source_path,
                output_path,
                logo_path=logo_path,
                border_width=50,
            )

            self.assertEqual(metadata["output_size"], [1080, 1350])
            self.assertEqual(metadata["rendered_size"], [312, 1250])
            self.assertEqual(metadata["source_box"], [384, 50, 696, 1300])

    def test_rejects_non_positive_border(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "tweet.png"
            output_path = root / "out.png"
            Image.new("RGB", (100, 100), "white").save(source_path)

            with self.assertRaisesRegex(ValueError, "at least 1"):
                brand_tweet_images.brand_tweet_image(
                    source_path,
                    output_path,
                    border_width=0,
                )


if __name__ == "__main__":
    unittest.main()
