"""Regression tests for upgrade-compatible new secure features."""
import json
import secrets
import tempfile
import unittest
from pathlib import Path
from veil.core import Vault, VaultError, record_login_event

class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.password = 'A long long test passphrase 2026!'
        self.v, self.key = Vault.create(self.root, self.password)
        self.orig = self.root / 'plaintext-test.txt'
        self.orig.write_bytes(secrets.token_bytes(3072))

    def test_bulk_move_transaction_and_original_ciphertext(self):
        v = self.v
        v.create_folder('A'); v.create_folder('B')
        x=v.import_file(self.orig,'A/first.txt')['file_id']
        y=v.import_file(self.orig,'A/second.txt')['file_id']
        before = (v.path/'objects'/(x+'.bin')).read_bytes()
        self.assertEqual(v.move_many([x,y], 'B'), 2)
        self.assertEqual({x['path'] for x in v.list_files()}, {'B/first.txt','B/second.txt'})
        self.assertEqual((v.path/'objects'/(x+'.bin')).read_bytes(), before)
        # Atomic conflict: neither file should move when one collides.
        v.import_file(self.orig,'A/first.txt')
        with self.assertRaises(VaultError):
            v.move_many([x,y], 'A')
        self.assertEqual({item['path'] for item in v.list_files()},
                         {'A/first.txt','B/first.txt','B/second.txt'})

    def test_recycle_restore_and_purge_roundtrip(self):
        v = self.v
        v.create_folder('Docs')
        a = v.import_file(self.orig,'Docs/secret.txt')['file_id']
        v.trash_many(['dir:Docs',a])
        self.assertFalse(v.list_files())
        self.assertTrue((v.path/'objects'/(a+'.bin')).is_file())
        self.assertNotIn(b'secret', (v.path/'manifest.enc').read_bytes())
        ids = sorted([e['id'] for e in v.trash_entries()], key=lambda i:(not i.startswith('dir:'),i))
        self.assertEqual(v.restore_trash(ids), 2)
        self.assertEqual(v.list_files()[0]['path'],'Docs/secret.txt')
        v.trash_file(a)
        v.purge_trash([a])
        self.assertFalse((v.path/'objects'/(a+'.bin')).exists())

    def test_nested_implicit_folders_restore(self):
        v = self.v
        v.import_file(self.orig,'Photos/2026/secret.txt')
        v.trash_many(['dir:Photos'])
        entries = v.trash_entries()
        self.assertTrue(any(e['path']=='Photos' for e in entries))
        self.assertTrue(any(e['path']=='Photos/2026' for e in entries), 'lost implicit nested folders')
        ids = sorted([e['id'] for e in entries], key=lambda i:(not i.startswith('dir:'),i.count('/')))
        v.restore_trash(ids)
        self.assertEqual(v.list_files()[0]['path'],'Photos/2026/secret.txt')

    def test_backup_copy_and_integrity(self):
        v = self.v
        v.import_file(self.orig)
        dest = self.root / 'Backups'; dest.mkdir()
        path=v.backup_to(dest)
        self.assertTrue((path/'vault.json').is_file())
        opened = Vault.open_password(path, self.password)
        self.assertEqual(opened.read_small_file(opened.list_files()[0]['file_id']), self.orig.read_bytes())
        self.assertEqual(v.health_check()['errors'], [])
        restored = Vault.restore_backup_copy(path, self.root / 'Backups')
        reopened = Vault.open_password(restored, self.password)
        self.assertEqual(reopened.list_files()[0]['path'], self.orig.name)
        fid=v.list_files()[0]['file_id']
        obj=v.path/'objects'/(fid+'.bin')
        blob=bytearray(obj.read_bytes()); blob[25]^=1; obj.write_bytes(blob)
        self.assertEqual(len(v.health_check()['errors']), 1)
        self.assertEqual(opened.health_check()['errors'], [])

    def test_settings_persist_and_login_audit_encrypted(self):
        self.v.set_preferences(idle_minutes=5, lock_windows=False, lock_sleep=True)
        self.v.set_backup_schedule(24)
        self.assertEqual(self.v.preferences()['backup_hours'],24)
        self.v.lock()
        record_login_event(self.v.path, False)
        record_login_event(self.v.path, True)
        self.assertNotIn(b'unlock_failure', b''.join(p.read_bytes() for p in (self.v.path/'events').glob('*.evt')))
        unlocked = Vault.open_password(self.v.path,self.password)
        self.assertEqual(unlocked.preferences()['idle_minutes'],5)
        self.assertEqual({a['event'] for a in unlocked.activity()}, {'unlock_failure','unlock_success'})

    def test_multiple_named_vaults_and_independent_passwords(self):
        extra,_=Vault.create(self.root, 'Another independent password 2026!', name='VEIL_Vault_Work')
        self.assertNotEqual(extra.path,self.v.path)
        extra.import_file(self.orig)
        extra.lock(); self.v.lock()
        self.assertTrue(Vault.open_password(extra.path,'Another independent password 2026!').list_files())
        with self.assertRaises(VaultError):
            Vault.open_password(self.v.path,'Another independent password 2026!')

if __name__ == '__main__': unittest.main()
