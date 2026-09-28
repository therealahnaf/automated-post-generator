"""Replace existing website reel media with verified original tweet video clips."""
from pathlib import Path
import json
import os
import sys
import tempfile
import requests

ROOT = Path('/root/automated-post-generator')
sys.path.insert(0, str(ROOT))
from tools.news import content_archive
from tools.news.website_video import prepare_source_video

base = os.environ['CONTENT_API_URL'].rstrip('/')
response = requests.get(base + '/api/posts?workflow_type=reel&limit=100', timeout=30)
response.raise_for_status()
posts = response.json()['items']
metadata_by_hash = {}
for sidecar in (ROOT / '.automation/watcher/jobs').rglob('*.json'):
    try:
        data = json.loads(sidecar.read_text())
        if isinstance(data, dict) and data.get('source_video') and data.get('output_sha256'):
            metadata_by_hash[data['output_sha256']] = sidecar
    except (ValueError, OSError):
        continue
for post in posts:
    if post.get('video_origin') == 'original_x':
        print('Already migrated:', post['title'], flush=True)
        continue
    digest = post['media'][0]['sha256']
    metadata = metadata_by_hash.get(digest)
    if not metadata:
        print('MISSING SOURCE METADATA:', post['title'], flush=True)
        continue
    with tempfile.TemporaryDirectory(prefix='website-source-') as directory:
        source = prepare_source_video(metadata.with_suffix('.mp4'), Path(directory) / 'source.mp4')
        with source.open('rb') as handle:
            response = requests.put(base + f"/api/posts/{post['id']}/source-video",
                headers={'Authorization': f"Bearer {os.environ['CONTENT_API_KEY']}"},
                data={'expected_sha256': digest}, files={'file': ('source.mp4', handle, 'video/mp4')},
                timeout=(10, 300))
        if not response.ok:
            raise RuntimeError(f'Source replacement failed: HTTP {response.status_code}')
        print('Migrated original tweet video:', post['title'], flush=True)
