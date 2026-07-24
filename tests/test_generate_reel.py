import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
        self.assertEqual(short_content, 10)
        self.assertEqual(short_total, 10)

    def test_outro_starts_at_fifteen_seconds(self) -> None:
        below_content, below_total = generate_reel.reel_timing(14.999)
        self.assertEqual(below_content, below_total)
        boundary_content, boundary_total = generate_reel.reel_timing(15)
        self.assertEqual(boundary_content, 12)
        self.assertEqual(boundary_total, 15)

    @patch("tools.reels.generate_reel.subprocess.run")
    def test_short_render_uses_only_headline_overlay(self, mock_run) -> None:
        mock_run.return_value = SimpleNamespace(returncode=0, stderr="")
        generate_reel.render_reel(
            Path("source.mp4"),
            Path("output.mp4"),
            {"headline": Path("headline.png")},
            content_end=10,
            total_duration=10,
            has_audio=False,
        )
        command = mock_run.call_args.args[0]
        filter_complex = command[command.index("-filter_complex") + 1]
        self.assertNotIn("coral", filter_complex)
        self.assertNotIn("typeout", " ".join(str(item) for item in command))
        self.assertEqual(command.count("-i"), 2)


if __name__ == "__main__":
    unittest.main()
