import unittest

from PIL import Image

from tools.news import codeastrix_footer


class CodeastrixFooterTests(unittest.TestCase):
    def test_applies_approved_footer_without_changing_canvas_size(self) -> None:
        source = Image.new("RGBA", (1080, 1350), (180, 20, 20, 255))
        result = codeastrix_footer.apply_footer(source)
        top = codeastrix_footer.footer_top(result.size)

        self.assertEqual(result.size, (1080, 1350))
        self.assertEqual(top, 1192)
        self.assertEqual(result.getpixel((500, top - 1)), (180, 20, 20, 255))
        self.assertNotEqual(result.getpixel((500, top + 20)), (180, 20, 20, 255))
        self.assertEqual(result.getpixel((500, 1349))[3], 255)

    def test_footer_layer_is_transparent_above_the_banner(self) -> None:
        layer = codeastrix_footer.make_footer_layer((1080, 1920))
        top = codeastrix_footer.footer_top(layer.size)

        self.assertEqual(layer.getpixel((500, top - 1))[3], 0)
        self.assertEqual(layer.getpixel((500, top + 20))[3], 255)

    def test_footer_scales_for_smaller_test_canvases(self) -> None:
        image = Image.new("RGBA", (540, 675), "white")
        result = codeastrix_footer.apply_footer(image)

        self.assertEqual(codeastrix_footer.footer_height(540), 79)
        self.assertEqual(codeastrix_footer.footer_top(result.size), 596)


if __name__ == "__main__":
    unittest.main()
