"""Attach missing website assets to a verified existing post; never publish socially."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

import requests

try:
    from .content_archive import original_photo_assets, background_assets
except ImportError:
    from content_archive import original_photo_assets, background_assets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post-id', required=True, type=UUID)
    parser.add_argument('--tweet-json', required=True, type=Path)
    parser.add_argument('--primary-image', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    base = os.environ['CONTENT_API_URL'].rstrip('/')
    response = requests.get(f'{base}/api/posts/{args.post_id}', timeout=30)
    response.raise_for_status()
    post = response.json()
    digest = hashlib.sha256(args.primary_image.read_bytes()).hexdigest()
    if digest not in {item['sha256'] for item in post['media']}:
        raise ValueError('Primary image does not match this published website post.')
    photos = original_photo_assets(json.loads(args.tweet_json.read_text(encoding='utf-8')))
    backgrounds = background_assets([args.primary_image])
    items = [dict(item, asset_type='x_photo') for item in photos] + backgrounds
    if not items:
        raise ValueError('No original photos or backgrounds found.')
    metadata = [{k: v for k, v in item.items() if k != 'path'} for item in items]
    print(json.dumps({'title': post['title'], 'photos': len(photos),
                      'backgrounds': len(backgrounds), 'apply': args.apply}))
    if args.apply:
        with ExitStack() as stack:
            files = [('files', (Path(item['path']).name,
                      stack.enter_context(Path(item['path']).open('rb')), 'application/octet-stream'))
                     for item in items]
            response = requests.post(f'{base}/api/posts/{args.post_id}/assets',
                headers={'Authorization': f"Bearer {os.environ['CONTENT_API_KEY']}"},
                data={'document': json.dumps({'assets': metadata})}, files=files, timeout=(10, 180))
            if not response.ok:
                raise RuntimeError(f'Asset repair failed: HTTP {response.status_code}')
            print('Website assets attached. Social posts unchanged.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
