from __future__ import annotations

import json
import secrets
import tempfile
import unittest
from pathlib import Path

from veil.core import Vault, VaultError


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.password = 'long secure test password 2026'
        self.vault, self.recovery = Vault.create(self.base, self.password)
        self.sample = self.base / 'photo.png'
        self.blob = secrets.token_bytes(712304)
        self.sample.write_bytes(self.blob)

    def test_empty_folder_created_persistently_and_encrypted(self):
        self.vault.create_folder('Vacations')
        self.vault.create_folder('Vacations/2026')
        self.assertEqual(self.vault.list_folders(), ['Vacations', 'Vacations/2026'])
        self.assertNotIn(b'Vacations', (self.vault.path / 'manifest.enc').read_bytes())
        self.vault.lock()
        reopened = Vault.open_password(self.vault.path, self.password)
        self.assertEqual(reopened.list_folders(), ['Vacations', 'Vacations/2026'])

    def test_import_move_and_rename_file_without_changing_ciphertext(self):
        self.vault.ensure_folder('Photos/Family')
        entry = self.vault.import_file(self.sample, 'Photos/Family/photo.png')
        encrypted = (self.vault.path / 'objects' / (entry['file_id'] + '.bin')).read_bytes()
        self.vault.create_folder('Private')
        self.vault.move_file(entry['file_id'], 'Private/pic secret.png')
        self.assertEqual(self.vault.list_files()[0]['path'], 'Private/pic secret.png')
        self.assertEqual((self.vault.path / 'objects' / (entry['file_id'] + '.bin')).read_bytes(), encrypted)
        target = self.base / 'restored.png'
        self.vault.export_file(entry['file_id'], target)
        self.assertEqual(target.read_bytes(), self.blob)

    def test_folder_move_and_rename_descendants(self):
        self.vault.ensure_folder('Pictures/2026/July')
        entry = self.vault.import_file(self.sample, 'Pictures/2026/July/photo.png')
        self.vault.create_folder('Private')
        self.vault.move_folder('Pictures', 'Private/Saved')
        self.assertEqual(self.vault.list_files()[0]['path'], 'Private/Saved/2026/July/photo.png')
        self.assertIn('Private/Saved/2026/July', self.vault.list_folders())
        self.assertNotIn('Pictures', self.vault.list_folders())
        self.vault.move_folder('Private/Saved/2026', 'Private/Saved/old')
        self.assertEqual(self.vault.list_files()[0]['path'], 'Private/Saved/old/July/photo.png')
        self.assertEqual(self.vault.read_small_file(entry['file_id']), self.blob)

    def test_no_recursive_folder_move_or_existing_name_collision(self):
        self.vault.ensure_folder('A/child')
        self.vault.create_folder('B')
        self.vault.import_file(self.sample, 'A/file.png')
        for old, dest in [('A', 'A/child/nested'), ('A', 'B')]:
            with self.subTest(old=old, dest=dest), self.assertRaises(VaultError):
                self.vault.move_folder(old, dest)
        with self.assertRaises(VaultError):
            self.vault.move_file(self.vault.list_files()[0]['file_id'], 'B')
        with self.assertRaises(VaultError):
            self.vault.create_folder('a')
        with self.assertRaises(VaultError):
            self.vault.create_folder('A/file.png')

    def test_recursive_delete_folder_only_removes_selected_subtree(self):
        self.vault.ensure_folder('A/one')
        self.vault.ensure_folder('B/other')
        item_a = self.vault.import_file(self.sample, 'A/one/private.bin')
        item_b = self.vault.import_file(self.sample, 'B/other/retain.bin')
        with self.assertRaises(VaultError):
            self.vault.delete_folder('A', recursive=False)
        deleted = self.vault.delete_folder('A', recursive=True)
        self.assertEqual(deleted, 1)
        self.assertNotIn('A', self.vault.list_folders())
        self.assertIn('B/other', self.vault.list_folders())
        self.assertFalse((self.vault.path / 'objects' / (item_a['file_id'] + '.bin')).exists())
        self.assertTrue((self.vault.path / 'objects' / (item_b['file_id'] + '.bin')).exists())

    def test_existing_v01_manifest_remains_compatible(self):
        item = self.vault.import_file(self.sample, 'Old/Nested/secret.bin')
        self.assertNotIn('folders', self.vault.manifest)
        self.vault.lock()
        recovered = Vault.open_password(self.vault.path, self.password)
        self.assertIn('Old/Nested', recovered.list_folders())
        recovered.create_folder('NewEmpty')
        recovered.move_file(item['file_id'], 'NewEmpty/secret.bin')
        self.assertEqual(recovered.read_small_file(item['file_id']), self.blob)
        recovered.lock()
        another = Vault.open_recovery(self.vault.path, self.recovery)
        self.assertIn('NewEmpty', another.list_folders())

    def test_self_import_blocked(self):
        original = self.vault.import_file(self.sample)
        ciphertext = self.vault.path / 'objects' / (original['file_id'] + '.bin')
        with self.assertRaises(VaultError):
            self.vault.import_file(ciphertext, 'reimported.bin')


if __name__ == '__main__':
    unittest.main()
