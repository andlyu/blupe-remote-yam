import hashlib
import tempfile
import unittest
from pathlib import Path
from remote_yam.artifacts import recorded_image

class ConversationImageTests(unittest.TestCase):
    def test_reads_only_content_addressed_images_and_rejects_links(self):
        with tempfile.TemporaryDirectory() as root:
            run_id = 'robocurve_' + 'a' * 32
            blobs = Path(root) / run_id / 'blobs'
            blobs.mkdir(parents=True)
            data = b'\xff\xd8fixture\xff\xd9'
            digest = hashlib.sha256(data).hexdigest()
            path = blobs / digest
            path.write_bytes(data)
            self.assertEqual(recorded_image(root, run_id, digest), (data, 'image/jpeg'))
            with self.assertRaises(ValueError): recorded_image(root, run_id, '../private')
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError): recorded_image(root, run_id, digest)
            path.unlink()
            outside = Path(root) / 'outside'; outside.write_bytes(data)
            path.symlink_to(outside)
            with self.assertRaises(ValueError): recorded_image(root, run_id, digest)
            path.unlink(); blobs.rmdir(); blobs.symlink_to(Path(root), target_is_directory=True)
            with self.assertRaises(ValueError): recorded_image(root, run_id, digest)
