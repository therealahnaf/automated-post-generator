import unittest

from tools.news import translate_carousel_copy as translator


class TranslateCarouselCopyTests(unittest.TestCase):
    def test_product_copy_places_intro_before_descriptions(self) -> None:
        values, is_product = translator.copy_strings(
            {
                "product_name": "Device",
                "intro_headline": "A useful device",
                "short_descriptions": ["First detail", "Second detail"],
            }
        )
        self.assertTrue(is_product)
        self.assertEqual(
            values,
            ["A useful device", "First detail", "Second detail"],
        )

    def test_parses_exact_ordered_bangla_array(self) -> None:
        translations = translator.parse_translations(
            '["প্রথম তথ্য", "দ্বিতীয় তথ্য"]',
            2,
        )
        self.assertEqual(translations, ["প্রথম তথ্য", "দ্বিতীয় তথ্য"])

    def test_rejects_wrong_translation_count(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "exactly 2"):
            translator.parse_translations('["একটি"]', 2)


if __name__ == "__main__":
    unittest.main()
