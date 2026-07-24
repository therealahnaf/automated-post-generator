import unittest

from tools.reels import generate_reel


class GenerateReelTests(unittest.TestCase):
    def test_rejects_non_x_video_hosts(self) -> None:
        self.assertTrue(
            generate_reel.valid_x_video_url(
                "https://video.twimg.com/ext_tw_video/example.mp4?tag=1"
            )
        )
        self.assertFalse(
            generate_reel.valid_x_video_url(
                "https://example.com/ext_tw_video/example.mp4"
            )
        )

    def test_prefers_best_format_below_download_ceiling(self) -> None:
        tweet = {
            "media": {
                "videos": [
                    {
                        "formats": [
                            {
                                "url": "https://video.twimg.com/a-low.mp4",
                                "container": "mp4",
                                "bitrate": 832000,
                            },
                            {
                                "url": "https://video.twimg.com/a-best.mp4",
                                "container": "mp4",
                                "bitrate": 2176000,
                            },
                            {
                                "url": "https://video.twimg.com/a-huge.mp4",
                                "container": "mp4",
                                "bitrate": 10368000,
                            },
                        ]
                    }
                ]
            }
        }
        selected = generate_reel.choose_video_format(tweet)
        self.assertEqual(selected["bitrate"], 2176000)

    def test_timing_caps_long_video_and_reserves_outro(self) -> None:
        content, total = generate_reel.reel_timing(63.62)
        self.assertEqual(content, 56.5)
        self.assertEqual(total, 59.5)
        short_content, short_total = generate_reel.reel_timing(10)
        self.assertEqual(short_content, 7)
        self.assertEqual(short_total, 10)


if __name__ == "__main__":
    unittest.main()
