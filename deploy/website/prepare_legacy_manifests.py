"""Add website sidecars to pending approvals without changing their approved copy.

Only uses explicit saved English titles. Ambiguous/missing data is reported for
manual review; this script never publishes, changes captions, or guesses copy.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path('/root/automated-post-generator')
sys.path.insert(0, str(ROOT))
from tools.news.content_archive import atomic_json, prepare_manifest
from tools.news.prepare_platform_descriptions import split_finalized_description

connection = sqlite3.connect(f'file:{ROOT}/.automation/watcher/watcher.sqlite3?mode=ro', uri=True)
for (job_id,) in connection.execute("select id from jobs where status='awaiting_approval'"):
    directory = ROOT / f'.automation/watcher/jobs/job-{job_id}'
    tweets = list(directory.rglob('tweet.json')) if directory.exists() else []
    if len(tweets) != 1:
        print(f'Job {job_id}: needs manual review (tweet metadata missing/ambiguous).')
        continue
    captions = list(directory.rglob('facebook-description.txt'))
    if not captions:
        print(f'Job {job_id}: needs manual review (standard caption missing).')
        continue
    titles = set()
    for path in directory.rglob('*.json'):
        try:
            data = json.loads(path.read_text())
            title = data.get('english_title') if isinstance(data, dict) else None
            if title and not any('\u0980' <= c <= '\u09ff' for c in title):
                titles.add(title)
        except (ValueError, OSError):
            continue
    if len(titles) != 1:
        print(f'Job {job_id}: needs manual review ({len(titles)} distinct saved English titles).')
        continue
    for caption in captions:
        try:
            english, _, _ = split_finalized_description(caption.read_text())
            manifest = prepare_manifest(title=next(iter(titles)), english=english, tweet_json=tweets[0], description_file=caption)
            manifest['caption_sha256'] = hashlib.sha256(caption.read_bytes()).hexdigest()
            target = caption.with_suffix('.website.json')
            if not target.exists():
                atomic_json(target, manifest)
            print(f'Job {job_id}: website sidecar ready; approved caption unchanged.')
        except Exception as exc:
            print(f'Job {job_id}: needs manual review ({type(exc).__name__}).')
