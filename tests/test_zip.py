"""ZIP archives are encrypted in place without unpacking or modifying them."""
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from veil.core import Vault
from veil.ui import VeilApp


class ZipImportTests(unittest.TestCase):
    def test_zip_roundtrip_bytes_and_password(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            src = base / 'Personal Archive.zip'
            with zipfile.ZipFile(src, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('Photos/picture.jpg', b'jpg' * 2000)
                archive.writestr('Docs/confidential.txt', 'Private document content')
            original = src.read_bytes()
            (base / 'vault-storage').mkdir()
            vault, key = Vault.create(base / 'vault-storage', 'Secure test password 12345!')
            vault.ensure_folder('My Archives')
            item = vault.import_file(src, 'My Archives/Personal Archive.zip')
            ciphertext = (vault.path / 'objects' / (item['file_id'] + '.bin')).read_bytes()
            self.assertNotIn(b'Private document content', ciphertext)
            self.assertNotIn(b'Personal Archive.zip', ciphertext)
            self.assertEqual(src.read_bytes(), original)
            vault.lock()
            reopened = Vault.open_password(vault.path, 'Secure test password 12345!')
            dest = base / 'Recovered Archive.zip'
            reopened.export_file(item['file_id'], dest)
            self.assertEqual(dest.read_bytes(), original)
            with zipfile.ZipFile(dest) as restored:
                self.assertEqual(sorted(restored.namelist()), ['Docs/confidential.txt', 'Photos/picture.jpg'])
                self.assertEqual(restored.read('Docs/confidential.txt'), b'Private document content')

    def test_add_zip_button_uses_standard_import(self):
        app = VeilApp.__new__(VeilApp)
        app.current_folder = 'Archive Storage'
        app._import_paths = Mock()
        with patch('veil.ui.filedialog.askopenfilenames', return_value=('C:/temp/backup.zip',)) as pick:
            app._add_zip()
        self.assertIn(('ZIP archives', '*.zip'), pick.call_args.kwargs['filetypes'])
        app._import_paths.assert_called_once_with(('C:/temp/backup.zip',), 'Archive Storage')

    def test_general_add_files_accepts_zip(self):
        app = VeilApp.__new__(VeilApp)
        app.current_folder = ''
        app._import_paths = Mock()
        with patch('veil.ui.filedialog.askopenfilenames', return_value=('D:/stuff/data.zip',)):
            app._add_files()
        app._import_paths.assert_called_once_with(('D:/stuff/data.zip',), '')


if __name__ == '__main__':
    unittest.main()
