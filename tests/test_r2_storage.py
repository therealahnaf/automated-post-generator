import hashlib
import io
import unittest
from unittest.mock import Mock, patch
from content_api import storage


class R2StorageTests(unittest.TestCase):
    def test_upload_is_content_addressed_and_verified(self):
        data = b'example'
        digest = hashlib.sha256(data).hexdigest()
        client = Mock()
        client.head_object.return_value = {'ContentLength': len(data), 'Metadata': {'sha256': digest}}
        client.get_object.return_value = {'Body': io.BytesIO(data)}
        with patch.object(storage, 'client', return_value=client), patch.object(storage, 'bucket', return_value='test'):
            key = storage.put(data, 'image/png', verify_download=True)
        self.assertEqual(key, f'media/{digest[:2]}/{digest}.png')
        self.assertEqual(client.put_object.call_args.kwargs['ContentType'], 'image/png')

    def test_mismatched_download_aborts_verification(self):
        data = b'example'
        digest = hashlib.sha256(data).hexdigest()
        client = Mock()
        client.head_object.return_value = {'ContentLength': len(data), 'Metadata': {'sha256': digest}}
        client.get_object.return_value = {'Body': io.BytesIO(b'wrong')}
        with patch.object(storage, 'client', return_value=client), patch.object(storage, 'bucket', return_value='test'):
            with self.assertRaisesRegex(RuntimeError, 'checksum'):
                storage.put(data, 'image/png', verify_download=True)

    def test_signed_links_expire(self):
        client = Mock()
        with patch.object(storage, 'client', return_value=client), patch.object(storage, 'bucket', return_value='test'):
            storage.url('media/a.mp4')
        self.assertEqual(client.generate_presigned_url.call_args.kwargs['ExpiresIn'], 3600)
