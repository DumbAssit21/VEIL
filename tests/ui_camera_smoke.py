"""Security-photo GUI regression for 720p; run with xvfb-run -a python tests/ui_camera_smoke.py."""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from veil.core import Vault, record_camera_snapshot
from veil.ui import VeilApp

with tempfile.TemporaryDirectory() as tmp:
    os.environ['LOCALAPPDATA'] = tmp
    vault, _ = Vault.create(Path(tmp), 'Strong test password 2026!!')
    vault.set_camera_enabled(True)
    image = Image.new('RGB', (800, 600), '#425e8a')
    out = io.BytesIO()
    image.save(out, format='JPEG')
    snapshot_id = record_camera_snapshot(vault.path, out.getvalue())

    app = VeilApp()
    app.folder = vault.path
    app.vault = vault
    app.root.geometry('1024x720')
    app.show_vault()
    app.notebook.select(3)  # Security photos
    app.root.update()

    assert snapshot_id in app.camera_list.get_children()
    assert app.camera_list.selection() == (snapshot_id,)
    assert app.capture_preview_photo is not None, 'Missing in-panel photo preview'
    assert app.camera_preview_label.cget('text') == '', 'Image not displayed'
    # Regression: photo actions were off-screen at 720p in v0.2.
    for button in (app.view_capture_button, app.delete_capture_button):
        assert button.winfo_ismapped(), f'{button.cget("text")} hidden'
        assert button.winfo_rooty() + button.winfo_height() <= (app.root.winfo_rooty() + app.root.winfo_height()), 'button clipped'

    if os.environ.get('VEIL_UI_SCREENSHOT'):
        from PIL import ImageGrab
        ImageGrab.grab().save(os.environ['VEIL_UI_SCREENSHOT'])
    app._view_capture()
    app.root.update()
    assert len(app.preview_windows) == 1, 'Full photo view not opened'
    assert app.preview_windows[0].winfo_exists()
    preview_window = app.preview_windows[0]
    app.lock()
    app.root.update()
    assert app.capture_preview_photo is None, 'Preview stays in memory after lock'
    assert not preview_window.winfo_exists(), 'Full image remains visible after lock'
    app.root.destroy()
print('PASS: login snapshots preview, open, toolbar visible at 720p, and close on lock')
