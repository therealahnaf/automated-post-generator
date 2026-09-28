"""Apply a reviewed archive-only patch, preserving production changes and secrets."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--enable', action='store_true')
args = parser.parse_args()
repo = Path('/root/automated-post-generator')
stage = Path('/opt/thebitstoday/integration')
backup = Path('/opt/thebitstoday/backups') / datetime.now(timezone.utc).strftime('integration-%Y%m%dT%H%M%SZ')
backup.mkdir(parents=True, mode=0o700)
if args.enable:
    from dotenv import dotenv_values, set_key
    env = repo / '.env'
    shutil.copy2(env, backup / 'publisher.env')
    (backup / 'publisher.env').chmod(0o600)
    values = dotenv_values('/opt/thebitstoday/app/deploy/website/.env')
    set_key(env, 'CONTENT_API_URL', 'http://127.0.0.1:18501')
    set_key(env, 'CONTENT_API_KEY', values['CONTENT_API_KEY'])
    env.chmod(0o600)
    print('Website delivery enabled; credentials remain on the VM.')
else:
    patch = stage / '.automation/deploy/archive-integration.patch'
    subprocess.run(['git', 'apply', '--check', '--ignore-space-change', str(patch)], cwd=repo, check=True)
    for name in re.findall(r'^\+\+\+ b/(.+)$', patch.read_text(), re.M):
        target = backup / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / name, target)
    destination = repo / 'tools/news/content_archive.py'
    if destination.exists():
        raise RuntimeError('Archive module already exists; review before replacing it.')
    # Install the new imported module before patching its consumers.
    shutil.copy2(stage / 'tools/news/content_archive.py', destination)
    subprocess.run(['git', 'apply', '--ignore-space-change', str(patch)], cwd=repo, check=True)
    print(f'Archive integration applied; originals backed up at {backup}.')
