"""VEIL v0.8 dark scrollbars and view layouts; requires a GUI display."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veil.core import Vault
from veil.ui import VeilApp, DarkScrollbar
with tempfile.TemporaryDirectory() as td:
    os.environ['LOCALAPPDATA'] = td
    base = Path(td)
    vault, _ = Vault.create(base, 'Scroll test password 2026!')
    vault.create_folder('Albums')
    for i in range(26):
        file = base / f'photo{i:03d}.txt'
        file.write_text(f'Test {i}')
        vault.import_file(file)
    app = VeilApp()
    app.folder, app.vault = vault.path, vault
    app.show_vault()
    for view in ['Details', 'Medium icons', 'Extra large icons', 'Tiles']:
        app.view_var.set(view)
        app._on_view_change()
        app.root.update()
        bars = [w for w in app.file_host.winfo_children()]
        assert bars
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        scrolls = [widget for widget in walk(app.file_host) if isinstance(widget, DarkScrollbar)]
        assert scrolls, f'No custom dark scrollbar in {view}'
        scroll = scrolls[0]
        scroll.set(0.0, 0.1)
        app.root.update()
        assert scroll._geometry()[2] >= 36
        scroll._move_to(100)
        app.root.update()
        assert app.vault is not None
    app.lock()
    app.root.destroy()
print('PASS: dark scrollbar and all four main view layouts')
