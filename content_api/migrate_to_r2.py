"""Resumable migration; verify R2 bytes before clearing any database blob."""
import hashlib
import psycopg
from psycopg.rows import dict_row
from .config import database_url
from . import storage


def main():
    migrated = 0
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        digests = conn.execute('SELECT sha256 FROM content_media WHERE data IS NOT NULL').fetchall()
        for entry in digests:
            with conn.transaction():
                row = conn.execute('SELECT * FROM content_media WHERE sha256=%s FOR UPDATE',
                                   (entry['sha256'],)).fetchone()
                if row['data'] is None:
                    continue
                data = bytes(row['data'])
                if hashlib.sha256(data).hexdigest() != row['sha256']:
                    raise RuntimeError('Database media checksum mismatch; migration stopped.')
                key = storage.put(data, row['mime_type'], verify_download=True)
                conn.execute('UPDATE content_media SET object_key=%s, data=NULL WHERE sha256=%s',
                             (key, row['sha256']))
            conn.commit()
            migrated += 1
            print(f'Migrated and verified {migrated}/{len(digests)} objects.', flush=True)
    print('R2 migration complete.')


if __name__ == '__main__':
    main()
