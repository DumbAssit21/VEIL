"""Create v0.9 screenshot using temporary, synthetic nonprivate data."""
import os,sys,tempfile
from pathlib import Path
from PIL import Image,ImageDraw,ImageGrab
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from veil.core import Vault
from veil.ui import VeilApp
with tempfile.TemporaryDirectory() as tmp:
    os.environ['LOCALAPPDATA']=tmp
    base=Path(tmp)
    v,_=Vault.create(base,'Screenshot test long password!')
    for name in ('Personal','Projects','Backups'):
        v.create_folder(name)
    for i in range(7):
        f=base/f'photo_{i:02}.png'
        im=Image.new('RGB',(300,190),(40+i*5,75+i*9,110+i*7))
        ImageDraw.Draw(im).text((35,80),f'Photo {i}',fill=(240,240,250))
        im.save(f);v.import_file(f)
    binary=base/'encrypted-archive.zip';binary.write_bytes(b'not a real zip, screenshot only')
    v.import_file(binary)
    app=VeilApp();app.root.geometry('1280x800+0+0')
    app.folder,app.vault=v.path,v
    app.show_vault()
    app.view_var.set('Details');app._on_view_change()
    f=next(e['file_id'] for e in v.list_files() if e['path'].endswith('png'));app.selected_id=f;app.selected_ids={f}
    app._refresh_files();app.root.update();app.root.update_idletasks()
    ImageGrab.grab(bbox=(0,0,1280,800)).save('/mnt/data/veil_v09/VEIL-v0.9-preview.png')
    app.lock();app.root.destroy()
print('saved preview')
