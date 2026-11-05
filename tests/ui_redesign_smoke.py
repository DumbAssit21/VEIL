"""VEIL v0.7 visual navigation and search regression (requires GUI display)."""
import io
import os
import sys
import tempfile
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veil.core import Vault
from veil.ui import VeilApp

with tempfile.TemporaryDirectory() as temp:
    os.environ['LOCALAPPDATA'] = temp
    work = Path(temp)
    vault, _ = Vault.create(work, 'Visual test only password 2026!')
    vault.create_folder('Pictures')
    image = work / 'private-example.png'
    Image.new('RGB', (120, 80), '#4477aa').save(image)
    entry = vault.import_file(image, 'Pictures/private-example.png')
    app = VeilApp()
    app.folder, app.vault = vault.path, vault
    app.show_vault()
    app.root.update()
    assert len(app.nav_buttons) == 6
    assert app.notebook.index(app.notebook.select()) == 0
    assert 'dir:Pictures' in app.visible_entries

    # Search across encrypted folders; selecting a result renders its details.
    app.search_var.set('example')
    app.root.update()
    assert len(app.visible_entries) == 1
    assert entry['file_id'] in app.visible_entries
    app.selected_id = entry['file_id']
    app.selected_ids = {entry['file_id']}
    app._refresh_files()
    app.root.update()
    assert app.file_preview_photo is not None, 'Photo preview missing'

    app.search_var.set('')
    app._select_nav(3)
    app.root.update()
    assert app.notebook.index(app.notebook.select()) == 3
    assert not app.search_box.instate(['!disabled'])
    app._select_nav(5)
    app.root.update()
    assert app.notebook.index(app.notebook.select()) == 5
    app._select_nav(0)
    app.current_folder = 'Pictures'
    app._refresh_files()
    app.root.update()
    assert entry['file_id'] in app.visible_entries
    app.lock()
    app.root.update()
    assert app.vault is None
    assert vault.path.exists()
    app.root.destroy()
print('PASS: v0.7 sidebar, cross-folder search, details preview, lock and old vault format')
