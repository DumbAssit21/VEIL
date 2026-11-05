"""Smoke test: six-tab UX + icon selection + recycle + backup settings (Xvfb)."""
import os, tempfile, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from veil.core import Vault
from veil.ui import VeilApp, messagebox
messagebox.showinfo = lambda *args,**kw: None
messagebox.showwarning = lambda *args,**kw: (_ for _ in ()).throw(AssertionError('warning: '+str(args)))
messagebox.showerror = lambda *args,**kw: (_ for _ in ()).throw(AssertionError('error: '+str(args)))
with tempfile.TemporaryDirectory() as tmp:
    os.environ['LOCALAPPDATA'] = tmp
    root = Path(tmp)
    vault, _ = Vault.create(root, 'Visual test long passphrase 2026!')
    vault.create_folder('Photos')
    for idx in range(3):
        image = root / f'picture-{idx}.png'
        Image.new('RGB',(100,100),(30+idx*20,70,155)).save(image)
        vault.import_file(image, 'Photos/' + image.name)
    app = VeilApp()
    app.folder, app.vault = vault.path, vault
    app.show_vault(); app.root.update()
    assert len(app.nav_buttons)==6
    app.current_folder='Photos'; app._refresh_files()
    assert len(app.visible_entries)==3
    app._select_all(); app.root.update()
    assert len(app.selected_ids)==3
    app.vault.trash_many(list(app.selected_ids))
    app.selected_ids=set(); app.selected_id=None
    app._refresh_files(); app.root.update()
    assert not app.visible_entries
    app._select_nav(2); app.root.update()
    assert len(app.recycle_tree.get_children())==3
    app._restore_trash(all_items=True); app.root.update()
    assert len(app.vault.list_files())==3
    app._select_nav(1); app.root.update()
    assert len(app.gallery_inner.winfo_children())==3
    app._select_nav(4); app.root.update()
    assert len(app.activity_tree.get_children()) >=2
    app._select_nav(5); app.root.update()
    assert app.idle_var.get()=='10'
    assert app.backup_cadence.get()=='Off'
    app.lock(); app.root.update()
    assert app.vault is None
    app.root.destroy()
print('PASS six tabs, bulk selection, recycle bin, gallery, activity, settings, lock')
