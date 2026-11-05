"""v0.6: encrypted ZIP browser with bounded in-memory video preview."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from veil.archives import ZipCatalog, safe_archive_path
from veil.core import Vault, VaultError


class ZipBrowseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.vault_root = cls.root / 'storage'
        cls.vault_root.mkdir()
        cls.vault, cls.recovery = Vault.create(cls.vault_root, 'Long test password for ZIP browsing!')
        cls.zip_path = cls.root / 'Photos.zip'
        with zipfile.ZipFile(cls.zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('Photos/2026/test.txt', b'Secret image description')
            z.writestr('Photos/2026/sub/readme.md', b'hello world')
            z.writestr('empty/', b'')
            z.writestr('Photos/2026/IMG_0253.MP4', b'mock MP4 content for preview size/path tests')
            z.writestr('../outside.txt', b'attack')
            z.writestr('/absolute.txt', b'attack')
            z.writestr('Windows\\trick.txt', b'attack')
        cls.item = cls.vault.import_file(cls.zip_path)

    @classmethod
    def tearDownClass(cls):
        cls.vault.lock()
        cls.temp.cleanup()

    def test_catalog_lists_nested_directories_and_readable_members(self):
        catalog = ZipCatalog(self.vault.read_verified_memory(self.item['file_id']))
        try:
            paths = [entry['path'] for entry in catalog.entries]
            self.assertIn('Photos', paths)
            self.assertIn('Photos/2026', paths)
            self.assertIn('Photos/2026/sub', paths)
            self.assertIn('empty', paths)
            self.assertIn('Photos/2026/test.txt', paths)
            self.assertNotIn('../outside.txt', paths)
            self.assertEqual(catalog.unsafe_count, 3)
            text_file = next(e for e in catalog.entries if e['path'].endswith('test.txt'))
            self.assertEqual(catalog.read_preview(text_file['index']), b'Secret image description')
        finally:
            catalog.close()
        self.assertTrue(catalog.memory is None)

    def test_no_plaintext_archive_written_to_vault_storage(self):
        original = self.zip_path.read_bytes()
        encrypted = (self.vault.path / 'objects' / (self.item['file_id'] + '.bin')).read_bytes()
        self.assertNotIn(b'Secret image description', encrypted)
        self.assertNotEqual(original, encrypted)
        self.assertFalse((self.vault.path / 'Photos.zip').exists())

    def test_member_export_requires_explicit_target_and_refuses_overwrite(self):
        catalog = ZipCatalog(self.vault.read_verified_memory(self.item['file_id']))
        try:
            index = next(e['index'] for e in catalog.entries if e['path'].endswith('readme.md'))
            path = self.root / 'chosen.md'
            self.assertEqual(catalog.export_member(index, path), path)
            self.assertEqual(path.read_bytes(), b'hello world')
            with self.assertRaises(VaultError):
                catalog.export_member(index, path)
            with self.assertRaises(VaultError):
                catalog.export_member(9999, self.root / 'nope.txt')
        finally:
            catalog.close()

    def test_corrupted_ciphertext_fails_authentication(self):
        filename = self.vault.path / 'objects' / (self.item['file_id'] + '.bin')
        original = filename.read_bytes()
        try:
            tampered = bytearray(original)
            tampered[len(tampered) // 2] ^= 0x02
            filename.write_bytes(tampered)
            with self.assertRaises(VaultError):
                self.vault.read_verified_memory(self.item['file_id'])
        finally:
            filename.write_bytes(original)

    def test_limit_prevents_unbounded_memory_use(self):
        with self.assertRaises(VaultError):
            self.vault.read_verified_memory(self.item['file_id'], limit=10)

    def test_unsafe_path_checks(self):
        for path in ('../a.txt', '/root/file', 'abc\\foo', 'C:/abc.txt', 'safe//not',
                     'a/./b', 'a/../b', 'x\ny'):
            self.assertIsNone(safe_archive_path(path), path)
        self.assertEqual(safe_archive_path('hello/there.jpg'), 'hello/there.jpg')

    def test_inner_file_previews_respect_byte_limit(self):
        catalogue = ZipCatalog(self.vault.read_verified_memory(self.item['file_id']))
        try:
            entry = next(e for e in catalogue.entries if e['kind'] == 'file')
            from unittest.mock import patch
            with patch('veil.archives.MAX_PREVIEW_BYTES', 2):
                with self.assertRaises(VaultError):
                    catalogue.read_preview(entry['index'])
        finally:
            catalogue.close()

    def test_video_members_read_only_into_memory(self):
        """ZIP video preview reads bytes without creating decrypted disk files."""
        catalog = ZipCatalog(self.vault.read_verified_memory(self.item['file_id']))
        try:
            info = next(e for e in catalog.entries if e['path'].endswith('IMG_0253.MP4'))
            self.assertEqual(catalog.read_video_preview(info['index']),
                             b'mock MP4 content for preview size/path tests')
            self.assertFalse((self.vault.path / 'IMG_0253.MP4').exists())
            self.assertFalse((self.root / 'IMG_0253.MP4').exists())
        finally:
            catalog.close()

    def test_video_preview_enforces_bounded_uncompressed_size(self):
        from unittest.mock import patch
        catalog = ZipCatalog(self.vault.read_verified_memory(self.item['file_id']))
        try:
            info = next(e for e in catalog.entries if e['path'].endswith('IMG_0253.MP4'))
            with patch('veil.archives.MAX_VIDEO_PREVIEW_BYTES', 8):
                with self.assertRaises(VaultError):
                    catalog.read_video_preview(info['index'])
        finally:
            catalog.close()

    def test_declared_video_formats_and_readonly_limit(self):
        from veil.media import VIDEO_EXTENSIONS, MAX_VIDEO_BYTES
        from veil.archives import MAX_VIDEO_PREVIEW_BYTES
        self.assertIn('.mp4', VIDEO_EXTENSIONS)
        self.assertIn('.mov', VIDEO_EXTENSIONS)
        self.assertEqual(MAX_VIDEO_BYTES, MAX_VIDEO_PREVIEW_BYTES)
        self.assertEqual(MAX_VIDEO_BYTES, 160 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
