"""Deploy the clean publisher checkout's exact commit; never copy loose source files."""
import argparse
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import urllib.request


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('commit', help='Full Git commit SHA already checked out in the publisher')
    args = parser.parse_args()
    repo = Path('/root/automated-post-generator')
    base = Path('/opt/thebitstoday')
    commit = run('git', 'rev-parse', 'HEAD', cwd=repo)
    if args.commit != commit or run('git', 'status', '--porcelain', '--untracked-files=no', cwd=repo):
        raise RuntimeError('Publisher must be clean and at the requested commit.')
    os.environ['RELEASE_SHA'] = commit
    release = base / 'releases' / commit
    release.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / 'source.tar'
        run('git', 'archive', '--format=tar', f'--output={archive}', commit, cwd=repo)
        with tarfile.open(archive) as bundle:
            bundle.extractall(release, filter='data')
    shared = base / 'shared'
    shared.mkdir(exist_ok=True, mode=0o700)
    app = base / 'app'
    # Move private configuration once; symlinks keep renewal and backup jobs stable.
    for name in ('.env', 'acme', 'letsencrypt'):
        old = app / 'deploy/website' / name
        target = shared / name
        if not target.exists() and old.exists():
            old.rename(target)
            old.symlink_to(target, target_is_directory=target.is_dir())
        link = release / 'deploy/website' / name
        if target.exists() and not link.is_symlink():
            link.symlink_to(target, target_is_directory=target.is_dir())
    compose = ['docker', 'compose', '--env-file', str(shared / '.env'),
               '-f', str(release / 'deploy/website/compose.yml')]
    print('Building release', commit, flush=True)
    subprocess.run(compose + ['build', 'api', 'web'], check=True)
    subprocess.run(['systemctl', 'start', 'thebitstoday-backup.service'], check=True)
    previous = app.resolve()
    if not app.is_symlink():
        previous = base / f'legacy-app-{int(time.time())}'
        app.rename(previous)
    pending = base / 'app-next'
    pending.symlink_to(release, target_is_directory=True)
    os.replace(pending, app)
    try:
        subprocess.run(compose + ['up', '-d', '--no-deps', 'api', 'web'], check=True)
        for attempt in range(60):
            try:
                with urllib.request.urlopen('http://127.0.0.1:18501/api/health', timeout=5) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(2)
        else:
            raise RuntimeError('API health check failed.')
        # nginx resolves the API upstream at startup; restart after API health.
        subprocess.run(compose + ['restart', 'web'], check=True)
        for attempt in range(30):
            try:
                with urllib.request.urlopen('http://127.0.0.1:18573/api/health', timeout=5) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(2)
        else:
            raise RuntimeError('Release health check failed.')
    except Exception:
        print('Deployment failed. Previous release preserved at', previous, flush=True)
        raise
    print('Healthy release:', commit, 'Previous release:', previous, flush=True)


if __name__ == '__main__':
    main()
