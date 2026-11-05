"""Visual smoke test: run with xvfb-run python tests/ui_smoke.py (not a security test)."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veil.core import Vault
from veil.ui import VeilApp

with tempfile.TemporaryDirectory() as d:
    os.environ['LOCALAPPDATA'] = d
    folder = Path(d)
    vault, recovery = Vault.create(folder, 'Strong test password 2026!!')
    vault.create_folder('Photos')
    vault.ensure_folder('Photos/2026')
    (folder / 'example.txt').write_text('test text')
    vault.import_file(folder / 'example.txt', 'Photos/2026/example.txt')
    app = VeilApp()
    app.vault = vault
    app.folder = vault.path
    app.show_vault()
    app.root.update()
    for mode in ('Details', 'List', 'Small icons', 'Medium icons', 'Large icons', 'Extra large icons', 'Tiles'):
        app.view_var.set(mode)
        app._on_view_change()
        app.root.update()
        assert 'dir:Photos' in app.visible_entries, (mode, app.visible_entries)
        app.current_folder = 'Photos'
        app._refresh_files()
        app.root.update()
        assert 'dir:Photos/2026' in app.visible_entries, mode
        app.current_folder = 'Photos/2026'
        app._refresh_files()
        app.root.update()
        assert any(x['name'] == 'example.txt' for x in app.visible_entries.values()), mode
        app.current_folder = ''
        app._refresh_files()
        app.root.update()
        print('GUI mode passed:', mode)
    app.lock()
    app.root.update()
    app.root.destroy()
print('UI smoke test complete')
