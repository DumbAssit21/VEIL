"""GUI wiring smoke, mocked decoder: xvfb-run -a python tests/ui_zip_video_smoke.py.

Does not verify real H264 decoding or sound hardware; test those on Windows.
"""
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veil.core import Vault
from veil.ui import VeilApp

class FakeVideo:
    def __init__(self, owner, raw, title, on_close):
        assert raw == b'MP4 memory-only payload'
        self.win = tk.Toplevel(owner)
        self.closed = False
        self.callback = on_close
    def close(self):
        if not self.closed:
            self.closed = True
            if self.win.winfo_exists():
                self.win.destroy()
            self.callback(self)

with tempfile.TemporaryDirectory() as d:
    os.environ['LOCALAPPDATA'] = d
    home = Path(d)
    zipped = home / 'family.zip'
    with zipfile.ZipFile(zipped, 'w') as z:
        z.writestr('Videos/IMG_0253.MP4', b'MP4 memory-only payload')
    vault, _ = Vault.create(home, 'Safe testing vault password!')
    item = vault.import_file(zipped)
    app = VeilApp()
    app.vault, app.folder = vault, vault.path
    app.show_vault()
    app.root.update()
    with patch('veil.ui.video_available', return_value=True), patch('veil.ui.MemoryVideoPlayer', FakeVideo):
        app._open_zip(item['file_id'])
        start = time.monotonic()
        ready = {'done': False}
        def check():
            if app.busy:
                assert time.monotonic() - start < 14, 'ZIP open timed out'
                app.root.after(80, check)
                return
            browser = next(w for w in app.preview_windows if isinstance(w, tk.Toplevel))
            tree = next(w for f in browser.winfo_children() for w in f.winfo_children()
                        if isinstance(w, ttk.Treeview))
            root_node = tree.get_children()[0]
            file_node = tree.get_children(root_node)[0]
            tree.selection_set(file_node)
            # Video preview button is the first child in the actions frame.
            buttons = [w for f in browser.winfo_children() if isinstance(f, ttk.Frame)
                       for w in f.winfo_children() if isinstance(w, ttk.Button)]
            assert buttons, 'No preview button'
            buttons[0].invoke()
            players = [w for w in app.preview_windows if isinstance(w, FakeVideo)]
            assert len(players) == 1, players
            player = players[0]
            app.lock()
            assert player.closed, 'Media window was not closed on vault lock'
            assert not player.win.winfo_exists(), 'Media window stayed visible on lock'
            ready['done'] = True
            app.root.quit()
        app.root.after(100, check)
        app.root.after(17000, app.root.quit)
        app.root.mainloop()
        assert ready['done']
    app.root.destroy()
print('PASS: MP4 preview dispatched in memory, closed on vault lock')
