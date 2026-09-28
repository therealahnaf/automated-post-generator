"""Initialize production secrets without printing or overwriting existing values."""
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent
path = root / '.env'
if not path.exists():
    with path.open('x', encoding='utf-8') as handle:
        os.chmod(path, 0o600)
        handle.write(f'POSTGRES_PASSWORD={secrets.token_hex(32)}\n')
        handle.write(f'CONTENT_API_KEY={secrets.token_hex(32)}\n')
print('Production environment initialized (values hidden).')
