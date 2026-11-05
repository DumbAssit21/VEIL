from __future__ import annotations

import json
import secrets
import tempfile
import unittest
from pathlib import Path

from veil.core import Vault, VaultError, record_camera_snapshot, FILE_MAGIC


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.password = 'This is my TEST passphrase 0001!'
        self.vault, self.recovery = Vault.create(self.base, self.password)

    def test_roundtrip_wrong_password_and_encrypted_names(self):
        original = self.base / 'photo confidential.jpg'
        content = secrets.token_bytes(350000)
        original.write_bytes(content)
        item = self.vault.import_file(original, 'Holiday/Private photo.jpg')
        self.assertTrue(item['file_id'])
        manifest = (self.vault.path / 'manifest.enc').read_bytes()
        blob = (self.vault.path / 'objects' / (item['file_id'] + '.bin')).read_bytes()
        self.assertNotIn(b'Holiday', manifest)
        self.assertNotIn(b'Private photo', blob)
        self.assertNotIn(content[:64], blob)
        self.assertTrue(blob.startswith(FILE_MAGIC))
        self.vault.lock()
        with self.assertRaises(VaultError):
            Vault.open_password(self.vault.path, 'wrong-password')
        again = Vault.open_password(self.vault.path, self.password)
        self.assertEqual(again.list_files()[0]['path'], 'Holiday/Private photo.jpg')
        out = self.base / 'restored_photo.jpg'
        again.export_file(item['file_id'], out)
        self.assertEqual(out.read_bytes(), content)

    def test_recovery_and_password_change(self):
        self.vault.lock()
        recovered = Vault.open_recovery(self.vault.path, self.recovery.lower().replace('-', ' '))
        recovered.change_password('Brand new secure phrase 2026!')
        recovered.lock()
        with self.assertRaises(VaultError):
            Vault.open_password(self.vault.path, self.password)
        opened = Vault.open_password(self.vault.path, 'Brand new secure phrase 2026!')
        self.assertEqual(opened.list_files(), [])
        opened.lock()
        again = Vault.open_recovery(self.vault.path, self.recovery)
        self.assertTrue(again.master)

    def test_tamper_detected_and_no_plaintext_destination(self):
        original = self.base / 'original.bin'
        original.write_bytes(secrets.token_bytes(200000))
        obj = self.vault.import_file(original)
        blob_path = self.vault.path / 'objects' / (obj['file_id'] + '.bin')
        blob = bytearray(blob_path.read_bytes())
        blob[40] ^= 0x1
        blob_path.write_bytes(blob)
        dest = self.base / 'export.bin'
        with self.assertRaises(VaultError):
            self.vault.export_file(obj['file_id'], dest)
        self.assertFalse(dest.exists())
        with self.assertRaises(VaultError):
            self.vault.read_small_file(obj['file_id'])

    def test_encrypted_camera_capture_before_unlock(self):
        jpeg_bytes = b'\xff\xd8' + secrets.token_bytes(512) + b'\xff\xd9'
        self.vault.set_camera_enabled(True)
        self.vault.lock()
        key = record_camera_snapshot(self.vault.path, jpeg_bytes)
        disk = (self.vault.path / 'captures' / (key + '.cap')).read_bytes()
        self.assertNotIn(jpeg_bytes[:20], disk)
        unlocked = Vault.open_password(self.vault.path, self.password)
        self.assertEqual(unlocked.open_camera_image(key), jpeg_bytes)
        self.assertEqual(len(unlocked.camera_entries()), 1)
        unlocked.delete_camera_image(key)
        self.assertEqual(unlocked.camera_entries(), [])

    def test_camera_off_disallows_capture(self):
        self.assertFalse(self.vault.config['capture_enabled'])
        with self.assertRaises(VaultError):
            record_camera_snapshot(self.vault.path, b'jpg')

    def test_path_traversal_and_duplicate_rejected(self):
        sample = self.base / 'example.txt'
        sample.write_text('Hello')
        for path in ('../secret', '/root/bad', 'folder/../../secret', 'C:/windows'):
            with self.subTest(path=path), self.assertRaises(VaultError):
                self.vault.import_file(sample, path)
        self.vault.import_file(sample, 'Safe/file.txt')
        with self.assertRaises(VaultError):
            self.vault.import_file(sample, 'safe/FILE.TXT')
        with self.assertRaises(VaultError):
            self.vault.export_file(self.vault.list_files()[0]['file_id'], self.vault.path / 'plaintext.txt')

    def test_deletion_and_empty_file(self):
        src = self.base / 'zero.txt'
        src.write_bytes(b'')
        item = self.vault.import_file(src)
        self.assertEqual(self.vault.read_small_file(item['file_id']), b'')
        out = self.base / 'zero_export.txt'
        self.vault.export_file(item['file_id'], out)
        self.assertEqual(out.stat().st_size, 0)
        with self.assertRaises(VaultError):
            self.vault.export_file(item['file_id'], out)
        self.vault.delete_file(item['file_id'])
        self.assertFalse(self.vault.list_files())

    def test_damaged_manifest_fails_closed(self):
        self.vault.lock()
        path = self.vault.path / 'manifest.enc'
        blob = bytearray(path.read_bytes())
        blob[-2] ^= 8
        path.write_bytes(blob)
        with self.assertRaises(VaultError):
            Vault.open_password(self.vault.path, self.password)


if __name__ == '__main__':
    unittest.main()
