"""Prepare a browser-playable original tweet video for the website, max 180s."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

MAX_SECONDS = 180.0


def probe(path: Path) -> dict:
    result = subprocess.run([shutil.which('ffprobe') or 'ffprobe', '-v', 'error',
        '-show_format', '-show_streams', '-of', 'json', str(path)],
        check=True, capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)


def prepare_source_video(approved_reel: Path, output: Path) -> Path:
    metadata = json.loads(approved_reel.with_suffix('.json').read_text(encoding='utf-8'))
    expected = metadata.get('output_sha256')
    if not expected or hashlib.sha256(approved_reel.read_bytes()).hexdigest() != expected:
        raise ValueError('Reel metadata does not match the approved video.')
    source = Path(metadata['source_video'])
    if not source.is_file():
        raise ValueError('Original tweet video is missing; cannot substitute the social reel.')
    expected_source = metadata.get('source_video_sha256')
    if not expected_source or hashlib.sha256(source.read_bytes()).hexdigest() != expected_source:
        raise ValueError('Original tweet video checksum does not match render metadata.')
    info = probe(source)
    duration = float(info['format']['duration'])
    if not 0 < duration:
        raise ValueError('Original video duration is invalid.')
    streams = info['streams']
    videos = [s for s in streams if s.get('codec_type') == 'video']
    audios = [s for s in streams if s.get('codec_type') == 'audio']
    compatible = videos and videos[0].get('codec_name') == 'h264' and all(s.get('codec_name') == 'aac' for s in audios)
    command = [shutil.which('ffmpeg') or 'ffmpeg', '-hide_banner', '-loglevel', 'error',
               '-nostdin', '-y', '-i', str(source), '-map', '0:v:0', '-map', '0:a:0?']
    if duration <= MAX_SECONDS and compatible and source.stat().st_size < 120 * 1024 * 1024:
        command += ['-c', 'copy']
    else:
        command += ['-t', str(MAX_SECONDS), '-c:v', 'libx264', '-preset', 'fast',
                    '-crf', '23', '-maxrate', '5M', '-bufsize', '10M', '-threads', '2',
                    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-b:a', '128k']
    command += ['-movflags', '+faststart', str(output)]
    subprocess.run(command, check=True, capture_output=True, timeout=1200)
    if float(probe(output)['format']['duration']) > MAX_SECONDS + 0.05:
        raise ValueError('Website video exceeds three minutes.')
    if output.stat().st_size > 128 * 1024 * 1024:
        raise ValueError('Website video exceeds the archive upload limit.')
    return output
