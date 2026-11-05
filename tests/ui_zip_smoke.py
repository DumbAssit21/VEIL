"""GUI ZIP browser smoke: run with xvfb-run -a python tests/ui_zip_smoke.py."""
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veil.core import Vault
from veil.ui import VeilApp

with tempfile.TemporaryDirectory() as d:
    os.environ['LOCALAPPDATA'] = d
    path = Path(d)
    zippath = path / 'Test Archive.zip'
    with zipfile.ZipFile(zippath, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('Pictures/2026/readme.txt', 'This is safe private text')
        z.writestr('Pictures/2026/nested/notes.json', '{"test":true}')
    vault, _ = Vault.create(path, 'Strong test password 2026!!')
    item = vault.import_file(zippath)
    app = VeilApp()
    app.vault, app.folder = vault, vault.path
    app.show_vault()
    app.root.update()
    app._open_zip(item['file_id'])
    started = time.monotonic()
    state = {'complete': False}

    def inspect():
        if app.busy:
            if time.monotonic() - started > 12:
                raise AssertionError('ZIP browser timed out')
            app.root.after(100, inspect)
            return
        tops = [w for w in app.preview_windows if w.winfo_exists()]
        assert len(tops) == 1, ('ZIP viewer missing', tops)
        win = tops[0]
        assert 'ZIP contents' in win.title(), win.title()
        trees = [w for w in win.winfo_children() for w in w.winfo_children() if isinstance(w, ttk.Treeview)]
        assert len(trees) == 1, ('No ZIP treeview', trees)
        tree = trees[0]
        roots = tree.get_children('')
        assert len(roots) == 1, roots
        assert 'Pictures' in tree.item(roots[0], 'text')
        assert tree.get_children(roots[0]), 'Expected 2026 subfolder'
        app.lock()
        assert not win.winfo_exists(), 'ZIP browser stays visible after lock'
        state['complete'] = True
        app.root.quit()

    app.root.after(100, inspect)
    app.root.after(15000, app.root.quit)
    app.root.mainloop()
    assert state['complete'], 'GUI smoke did not complete'
    app.root.destroy()
print('PASS: ZIP opens as nested folder tree, closes on vault lock')
