import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools.news import website_video


class WebsiteVideoTests(unittest.TestCase):
    def setup_files(self, directory):
        root = Path(directory)
        source, approved, output = root/'source.mp4', root/'reel.mp4', root/'website.mp4'
        source.write_bytes(b'original-tweet')
        approved.write_bytes(b'branded-reel')
        approved.with_suffix('.json').write_text(json.dumps({
            'source_video': str(source),
            'source_video_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'output_sha256': hashlib.sha256(approved.read_bytes()).hexdigest(),
        }))
        return source, approved, output

    def check_conversion(self, duration):
        with tempfile.TemporaryDirectory() as directory:
            source, approved, output = self.setup_files(directory)
            info = {'format': {'duration': str(duration)}, 'streams': [
                {'codec_type': 'video', 'codec_name': 'h264'}, {'codec_type': 'audio', 'codec_name': 'aac'}]}
            with patch.object(website_video, 'probe', side_effect=[info, {'format': {'duration': str(min(duration,180))}}]), \
                 patch.object(website_video.subprocess, 'run', side_effect=lambda *a, **k: output.write_bytes(b'website')) as run:
                website_video.prepare_source_video(approved, output)
            command = run.call_args.args[0]
            self.assertEqual(command[command.index('-i')+1], str(source))
            return command

    def test_short_video_uses_full_original_without_social_overlay(self):
        command = self.check_conversion(100)
        self.assertIn('copy', command)
        self.assertNotIn('-t', command)

    def test_long_video_is_trimmed_and_encoded_for_browser(self):
        command = self.check_conversion(400)
        self.assertEqual(command[command.index('-t')+1], '180.0')
        self.assertIn('libx264', command)
        self.assertIn('+faststart', command)

    def test_wrong_source_checksum_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source, approved, output = self.setup_files(directory)
            source.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                website_video.prepare_source_video(approved, output)
