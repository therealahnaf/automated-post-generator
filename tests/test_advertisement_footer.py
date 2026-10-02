import unittest

from PIL import Image

from tools.news import advertisement_footer as renderer
from tools.news import codeastrix_footer as legacy


class AdvertisementFooterTests(unittest.TestCase):
    def test_asset_and_proportional_scaling(self):
        with Image.open(renderer.DEFAULT_BANNER) as image:
            self.assertEqual(image.size, (2172, 233))
        for width, height in ((1080, 116), (540, 58), (2172, 233)):
            self.assertEqual(renderer.load_banner(width).size, (width, height))

    def test_banner_is_exact_and_does_not_change_content(self):
        source = Image.new("RGBA", (1080, 1350), "red")
        result = renderer.apply_footer(source)
        top = renderer.footer_top(source.size)
        self.assertEqual(result.crop((0, 0, 1080, top)).tobytes(), source.crop((0, 0, 1080, top)).tobytes())
        self.assertEqual(result.crop((0, top, 1080, 1350)).tobytes(), renderer.load_banner().tobytes())
        self.assertEqual(source.getpixel((0, 1349)), (255, 0, 0, 255))

    def test_custom_position_is_transparent_above_and_below(self):
        layer = renderer.make_footer_layer((1080, 1920), top=1264)
        self.assertEqual(layer.getpixel((0, 1263))[3], 0)
        self.assertEqual(layer.getpixel((0, 1380))[3], 0)
        self.assertEqual(layer.crop((0, 1264, 1080, 1380)).tobytes(), renderer.load_banner().tobytes())

    def test_invalid_sizes_and_positions_are_rejected(self):
        for size, top in (((100, 500), None), ((1080, 100), None), ((1080, 1350), -1), ((1080, 1350), 1300)):
            with self.subTest(size=size, top=top), self.assertRaises(ValueError):
                renderer.make_footer_layer(size, top=top)

    def test_cached_banner_cannot_be_mutated_by_callers(self):
        banner = renderer.load_banner()
        original_pixel = banner.getpixel((0, 0))
        banner.putpixel((0, 0), (0, 0, 0, 0))
        self.assertEqual(renderer.load_banner().getpixel((0, 0)), original_pixel)

    def test_legacy_entry_point_uses_the_same_advertisement(self):
        self.assertEqual(legacy.load_banner().tobytes(), renderer.load_banner().tobytes())


if __name__ == "__main__":
    unittest.main()
