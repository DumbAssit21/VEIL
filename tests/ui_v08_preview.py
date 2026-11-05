"""Generate v0.8 UI screenshot from a synthetic temporary vault."""
import os, sys, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageGrab
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veil.core import Vault
from veil.ui import VeilApp
with tempfile.TemporaryDirectory() as td:
    os.environ['LOCALAPPDATA'] = td
    tmp=Path(td)
    vault,_=Vault.create(tmp,'Screenshot test password 2026!')
    vault.create_folder('Photos')
    vault.create_folder('Projects')
    vault.create_folder('Backups')
    for i in range(11):
        f=tmp/f'IMG_{i:04}.png'
        im=Image.new('RGB',(200,160),(34+5*i,64+5*i,116+5*i))
        ImageDraw.Draw(im).text((22,65),f'Private image {i}',fill=(230,230,245))
        im.save(f)
        vault.import_file(f)
    app=VeilApp(); app.root.geometry('1380x820+0+0')
    app.folder,app.vault=vault.path,vault
    app.show_vault(); app.view_var.set('Details'); app._on_view_change()
    app.root.update()
    app.root.update_idletasks()
    ImageGrab.grab(bbox=(0,0,1380,820)).save('/mnt/data/veil_v08/VEIL-v0.8-ui-preview.png')
    app.lock(); app.root.destroy()
print('Saved screenshot')
