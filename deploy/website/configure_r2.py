"""Merge a private R2 dotenv file into production env files without echoing secrets."""
from pathlib import Path
import sys
from dotenv import dotenv_values, set_key

source = Path(sys.argv[1])
values = dotenv_values(source)
required = ('R2_ENDPOINT_URL', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET')
if not all(values.get(key) for key in required):
    raise ValueError('Incomplete R2 credentials.')
for target in (Path('/root/automated-post-generator/.env'),
               Path('/opt/thebitstoday/app/deploy/website/.env')):
    for key, value in values.items():
        if key.startswith('R2_') and value:
            set_key(target, key, value)
    target.chmod(0o600)
print('R2 settings stored in publisher and API deployment environments.')
