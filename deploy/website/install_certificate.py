"""Install this site's certificate without restarting the shared reverse proxy."""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import subprocess

source = Path('/opt/thebitstoday/app/deploy/website/letsencrypt/live/thebitstoday.com')
cert = source / 'fullchain.pem'
key = source / 'privkey.pem'
for hostname in ('thebitstoday.com', 'www.thebitstoday.com'):
    subprocess.run(['openssl', 'x509', '-in', str(cert), '-noout', '-checkhost', hostname], check=True)
subprocess.run(['openssl', 'x509', '-in', str(cert), '-noout', '-checkend', '86400'], check=True)
config = Path('/root/app/traefik/dynamic.yml')
text = config.read_text()
# Refuse to append inside an unknown YAML section.
if not re.fullmatch(r'tls:\s*\n  certificates:\s*\n(?:\s*(?:#.*|-(?: certFile:).*|keyFile:.*)?\n)*', text):
    raise RuntimeError('Shared TLS configuration has changed; manual review required.')
destination = Path('/root/app/traefik/certs')
target_cert = destination / 'thebitstoday.crt'
target_key = destination / 'thebitstoday.key'
if target_cert.exists() and target_cert.read_bytes() == cert.read_bytes() and '/certs/thebitstoday.crt' in text:
    print('Certificate is already installed.')
else:
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('/opt/thebitstoday/backups') / f'tls-{stamp}'
    backup.mkdir(mode=0o700, parents=True)
    shutil.copy2(config, backup / 'dynamic.yml')
    for target in (target_cert, target_key):
        if target.exists():
            shutil.copy2(target, backup / target.name)
    shutil.copyfile(cert, target_cert)
    shutil.copyfile(key, target_key)
    target_cert.chmod(0o644)
    target_key.chmod(0o600)
    if '/certs/thebitstoday.crt' not in text:
        text = text.rstrip() + '\n    - certFile: /certs/thebitstoday.crt\n      keyFile: /certs/thebitstoday.key\n'
    text = re.sub(r'^\s*# The Bits Today certificate updated.*\n', '', text, flags=re.M)
    text += f'    # The Bits Today certificate updated {stamp}\n'
    # This file is individually bind-mounted: preserve its inode while updating.
    with config.open('r+') as handle:
        handle.write(text)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
    print('Certificate installed; shared proxy reloads its TLS configuration automatically.')
