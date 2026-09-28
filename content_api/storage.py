"""Private R2 object storage. Keys are content-addressed; credentials stay server-side."""
from functools import lru_cache
import hashlib
import os

import boto3
from botocore.config import Config


@lru_cache(maxsize=1)
def client():
    return boto3.client('s3', endpoint_url=os.environ['R2_ENDPOINT_URL'],
        aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'], region_name='auto',
        config=Config(signature_version='s3v4', connect_timeout=10, read_timeout=180,
                      retries={'max_attempts': 3}, s3={'addressing_style': 'path'},
                      request_checksum_calculation='when_required',
                      response_checksum_validation='when_required'))


def bucket():
    return os.environ['R2_BUCKET']


def put(data: bytes, mime: str, *, verify_download: bool = False) -> str:
    digest = hashlib.sha256(data).hexdigest()
    extension = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp',
                 'video/mp4': 'mp4'}[mime]
    key = f'media/{digest[:2]}/{digest}.{extension}'
    client().put_object(Bucket=bucket(), Key=key, Body=data, ContentType=mime,
                        CacheControl='public, max-age=31536000, immutable',
                        Metadata={'sha256': digest})
    head = client().head_object(Bucket=bucket(), Key=key)
    if head['ContentLength'] != len(data) or head.get('Metadata', {}).get('sha256') != digest:
        raise RuntimeError('R2 upload verification failed.')
    if verify_download and hashlib.sha256(read(key)).hexdigest() != digest:
        raise RuntimeError('R2 download checksum verification failed.')
    return key


def read(key: str) -> bytes:
    body = client().get_object(Bucket=bucket(), Key=key)['Body']
    try:
        return body.read()
    finally:
        body.close()


def url(key: str) -> str:
    return client().generate_presigned_url('get_object',
        Params={'Bucket': bucket(), 'Key': key}, ExpiresIn=3600)


def save(conn, digest: str, mime: str, data: bytes) -> None:
    existing = conn.execute('SELECT object_key FROM content_media WHERE sha256 = %s',
                            (digest,)).fetchone()
    if existing and (existing['object_key'] if isinstance(existing, dict) else existing[0]):
        return
    key = put(data, mime)
    conn.execute('''INSERT INTO content_media(sha256, mime_type, data, byte_size, object_key)
                    VALUES (%s,%s,NULL,%s,%s) ON CONFLICT (sha256) DO UPDATE
                    SET object_key = EXCLUDED.object_key, data = NULL''',
                 (digest, mime, len(data), key))
