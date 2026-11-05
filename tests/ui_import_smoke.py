"""Headless visual import smoke, run xvfb-run -a python tests/ui_import_smoke.py."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veil.core import Vault
from veil.ui import VeilApp

with tempfile.TemporaryDirectory() as d:
    base = Path(d)
    os.environ['LOCALAPPDATA'] = d
    vault, _ = Vault.create(base, 'Demo long secure password 2026')
    sample = base / 'Images folder with spaces'
    sample.mkdir()
    (sample / 'empty child').mkdir()
    original = sample / 'photo sample.jpg'
    original.write_bytes(b'fake jpg bytes for encrypted roundtrip')
    app = VeilApp()
    app.vault = vault
    app.folder = vault.path
    app.show_vault()
    app.view_mode = 'Small icons'
    app.view_var.set('Small icons')
    app._refresh_files()
    def start_import():
        app._import_paths([str(sample)], '')
        app.root.after(90, finish_when_ready)
    def finish_when_ready():
        if app.busy:
            app.root.after(90, finish_when_ready)
            return
        assert 'Images folder with spaces' in vault.list_folders()
        assert 'Images folder with spaces/empty child' in vault.list_folders()
        entries = vault.list_files()
        assert len(entries) == 1
        assert entries[0]['path'] == 'Images folder with spaces/photo sample.jpg'
        assert vault.read_small_file(entries[0]['file_id']) == original.read_bytes()
        app.current_folder = 'Images folder with spaces'
        app._refresh_files()
        assert any(entry['name'] == 'empty child' for entry in app.visible_entries.values())
        assert any(entry['name'] == 'photo sample.jpg' for entry in app.visible_entries.values())
        app.lock()
        app.root.destroy()
        print('folder import and in-app organization passed')
    app.root.after(120, start_import)
    app.root.mainloop()
