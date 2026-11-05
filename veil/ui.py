"""Windows-first desktop interface for VEIL (Tkinter + TkDND)."""
from __future__ import annotations

import io
import webbrowser
import datetime as dt
import ctypes
import json
import os
import shutil
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except ImportError:
    TkinterDnD = None
    DND_FILES = None
from tkinter import filedialog, messagebox, simpledialog, ttk

from .core import Vault, VaultError, record_camera_snapshot, record_login_event
from .archives import ZipCatalog
from .media import MemoryVideoPlayer, VIDEO_EXTENSIONS, video_available
from .camera import capture_jpeg
from .version import __version__, GITHUB_RELEASES_URL
from .updates import find_update

BG = '#0a101e'
SIDEBAR = '#0d1a2d'
PANEL = '#121e30'
PANEL2 = '#1b2b41'
HEADER = '#202e42'
BORDER = '#2a3d55'
FG = '#f4f7fc'
MUTED = '#9cacc3'
ACCENT = '#57acfb'
ACTIVE = '#234a73'
SUCCESS = '#79e7a0'
FONT = 'Segoe UI'


class DarkScrollbar(tk.Canvas):
    """A theme-independent, narrow dark scrollbar for Treeview and Canvas.

    Native Windows scrollbar troughs may ignore ttk theme colors. Drawing our
    own scrollbar keeps the file browser dark without changing its scrolling.
    """

    def __init__(self, master, *, command, width=12, **kwargs):
        super().__init__(master, width=width, bg=PANEL, bd=0, relief='flat',
                         highlightthickness=0, takefocus=0, **kwargs)
        self.command = command
        self._start = 0.0
        self._end = 1.0
        self._drag_offset = None
        self.bind('<Configure>', lambda _e: self._redraw())
        self.bind('<Button-1>', self._press)
        self.bind('<B1-Motion>', self._drag)
        self.bind('<ButtonRelease-1>', lambda _e: setattr(self, '_drag_offset', None))

    def set(self, first, last):
        try:
            self._start = max(0.0, min(1.0, float(first)))
            self._end = max(self._start, min(1.0, float(last)))
        except (TypeError, ValueError):
            return
        self._redraw()

    def _geometry(self):
        h = max(1, self.winfo_height())
        visible = self._end - self._start
        if visible >= 0.999:
            return h, 0, h
        length = max(36, int(h * visible))
        length = min(h, length)
        range_px = max(0, h - length)
        position = self._start / max(0.000001, 1 - visible)
        top = max(0, min(range_px, round(range_px * position)))
        return h, top, length

    def _redraw(self):
        if not self.winfo_exists():
            return
        self.delete('all')
        w = max(1, self.winfo_width())
        h, y, length = self._geometry()
        self.create_rectangle(0, 0, w, h, fill=PANEL, outline='')
        if self._end - self._start >= 0.999:
            return
        x1, x2 = 2, max(3, w - 2)
        color = '#526d89'
        radius = min(4, (x2 - x1) / 2, length / 2)
        self.create_rectangle(x1, y + radius, x2, y + length - radius, fill=color, outline='')
        self.create_oval(x1, y, x2, y + radius * 2, fill=color, outline='')
        self.create_oval(x1, y + length - radius * 2, x2, y + length, fill=color, outline='')

    def _move_to(self, top):
        h, _y, length = self._geometry()
        if h <= length or self._end - self._start >= 0.999:
            return
        frac = max(0., min(1., float(top) / (h - length)))
        max_start = max(0., 1 - (self._end - self._start))
        self.command('moveto', frac * max_start)

    def _press(self, event):
        _h, top, length = self._geometry()
        if top <= event.y <= top + length:
            self._drag_offset = event.y - top
        else:
            self._drag_offset = length / 2
            self._move_to(event.y - self._drag_offset)

    def _drag(self, event):
        if self._drag_offset is not None:
            self._move_to(event.y - self._drag_offset)


def recent_state_file() -> Path:
    base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.config'))
    return base / 'VEIL' / 'state.json'


class VeilApp:
    def __init__(self):
        self.root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
        self.root.title(f'VEIL | Private encrypted vault — v{__version__}')
        if os.name == 'nt':
            try:
                self.root.iconbitmap(str(Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent)) / 'assets' / 'VEIL.ico'))
            except tk.TclError:
                pass
        self.root.geometry('1500x865')
        self.root.minsize(1000, 640)
        self.root.configure(bg=BG)
        self.vault: Vault | None = None
        self.folder: Path | None = None
        self.captured_this_session: set[Path] = set()
        self.last_activity = time.monotonic()
        self.inactivity_seconds = 10 * 60
        self.current_folder = ''
        self.view_mode = 'Details'
        self.busy = False
        self.selected_id = None
        self.selected_ids = set()
        self._selection_anchor = None
        self._thumbnail_cache = {}
        self._tile_widgets = {}
        self.preview_windows = []
        self.file_preview_photo = None
        self._ui_icons = {}
        self._tree_icon_cache = {}
        self._active_nav = 0
        self.search_var = tk.StringVar(value='')
        self.capture_preview_photo = None
        self._previous_tick = time.monotonic()
        self._backup_in_progress = False
        self.auto_check_updates = False
        self._style()
        self.root.bind_all('<Key>', self._activity, add='+')
        self.root.bind_all('<Button>', self._activity, add='+')
        self.root.after(5 * 1000, self._inactivity_check)
        self.root.protocol('WM_DELETE_WINDOW', self._quit)
        try:
            state = json.loads(recent_state_file().read_text('utf8'))
            self.auto_check_updates = state.get('auto_check_updates') is True
            saved = state.get('vault')
            if state.get('view') in ('Details', 'List', 'Small icons', 'Medium icons',
                                     'Large icons', 'Extra large icons', 'Tiles'):
                self.view_mode = state['view']
            if saved:
                self.folder = Vault.locate(saved)
        except (OSError, ValueError, VaultError):
            pass
        self.show_login()
        if self.auto_check_updates:
            self.root.after(4000, lambda: self._check_updates(show_current=False))

    def _style(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TFrame', background=BG)
        style.configure('Card.TFrame', background=PANEL)
        style.configure('TLabel', background=BG, foreground=FG, font=(FONT, 10))
        style.configure('Card.TLabel', background=PANEL, foreground=FG, font=(FONT, 10))
        style.configure('TButton', background=PANEL2, foreground=FG, relief='flat', borderwidth=1,
                        bordercolor=BORDER, lightcolor=PANEL2, darkcolor=PANEL2,
                        padding=(12, 10), font=(FONT, 10))
        style.map('TButton', background=[('active', '#2a4667'), ('disabled', PANEL)],
                  foreground=[('disabled', '#697d96')])
        style.configure('Accent.TButton', background='#1762ad', foreground=FG, relief='flat',
                        borderwidth=1, bordercolor=ACCENT, padding=(15, 11), font=(FONT, 10, 'bold'))
        style.map('Accent.TButton', background=[('active', '#237bcf')])
        style.configure('Danger.TButton', background='#382434', foreground='#ffb9be',
                        borderwidth=1, bordercolor='#644351', padding=(12, 10), font=(FONT, 10))
        style.map('Danger.TButton', background=[('active', '#5c2e40')])
        style.configure('TEntry', fieldbackground=PANEL2, foreground=FG, insertcolor=FG,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        borderwidth=1, padding=10)
        style.configure('TCombobox', fieldbackground=PANEL2, background=PANEL2,
                        foreground=FG, arrowcolor=MUTED, padding=(8, 7),
                        bordercolor=BORDER, lightcolor=PANEL2, darkcolor=PANEL2, relief='flat')
        style.map('TCombobox', fieldbackground=[('readonly', PANEL2)],
                  selectbackground=[('readonly', PANEL2)], selectforeground=[('readonly', FG)])
        style.configure('Hidden.TNotebook', background=BG, borderwidth=0, padding=0, relief='flat',
                        bordercolor=BG, lightcolor=BG, darkcolor=BG)
        style.layout('Hidden.TNotebook', [('Notebook.client', {'sticky': 'nswe', 'border': 0})])
        style.layout('Hidden.TNotebook.Tab', [])
        style.configure('Hidden.TNotebook.Tab', padding=0, borderwidth=0)
        style.configure('Treeview', background=PANEL, fieldbackground=PANEL,
                        foreground=FG, borderwidth=0, bordercolor=BORDER,
                        lightcolor=PANEL, darkcolor=PANEL, relief='flat', rowheight=39,
                        font=(FONT, 10), indent=3)
        style.configure('Treeview.Heading', background=HEADER, foreground='#e7effb',
                        bordercolor=BORDER, lightcolor=HEADER, darkcolor=HEADER,
                        relief='flat', borderwidth=0, padding=11, font=(FONT, 10, 'bold'))
        style.map('Treeview', background=[('selected', ACTIVE)],
                  foreground=[('selected', FG)])
        style.configure('TCheckbutton', background=PANEL, foreground=FG, font=(FONT, 10))
        style.map('TCheckbutton', background=[('active', PANEL)], foreground=[('active', FG)])
        style.configure('TScrollbar', background=PANEL2, troughcolor=PANEL, arrowcolor=MUTED)


    def _activity(self, _event=None):
        self.last_activity = time.monotonic()

    def _windows_locked(self):
        """Best-effort Windows secure desktop detection. Fail open if API unavailable."""
        if os.name != 'nt':
            return False
        try:
            user32 = ctypes.windll.user32
            user32.OpenInputDesktop.restype = ctypes.c_void_p
            handle = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
            if not handle:
                return True
            user32.CloseDesktop(ctypes.c_void_p(handle))
        except Exception:
            return False
        return False

    def _inactivity_check(self):
        now = time.monotonic()
        elapsed = now - self._previous_tick
        self._previous_tick = now
        if self.vault and not self.busy:
            opts = self.vault.preferences()
            minutes = int(opts.get('idle_minutes', 10))
            if (opts.get('lock_windows', True) and self._windows_locked()) or (opts.get('lock_sleep', True) and elapsed > 90) or (minutes and now - self.last_activity > minutes * 60):
                self.lock()
            else:
                self._maybe_auto_backup()
        self.root.after(5 * 1000, self._inactivity_check)

    def _quit(self):
        if self.busy:
            messagebox.showwarning('VEIL', 'A file import is still running. Wait until it finishes before closing.')
            return
        self._close_previews()
        self._clear_capture_preview()
        if self.vault:
            self.vault.lock()
        self.root.destroy()

    def run(self):
        self.root.mainloop()

    def _clear(self):
        for child in self.root.winfo_children():
            child.destroy()

    def _header(self, subtitle: str):
        area = ttk.Frame(self.root, padding=(30, 24, 30, 8))
        area.pack(fill='x')
        tk.Label(area, text='◈  VEIL', bg=BG, fg=FG,
                 font=(FONT, 26, 'bold')).pack(anchor='w')
        tk.Label(area, text=subtitle, bg=BG, fg=MUTED,
                 font=(FONT, 11)).pack(anchor='w', pady=(3, 0))

    def _note(self, parent, text: str, color=MUTED):
        label = tk.Label(parent, text=text, fg=color, bg=PANEL, font=(FONT, 10),
                         wraplength=660, justify='left')
        label.pack(anchor='w', pady=6)
        return label

    def _save_recent(self):
        if self.folder:
            p = recent_state_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            old = {}
            try:
                old = json.loads(p.read_text('utf8'))
            except (OSError, ValueError):
                pass
            recent = [str(self.folder)] + [v for v in old.get('recent', []) if v != str(self.folder)]
            old.update({'vault': str(self.folder), 'view': self.view_mode, 'recent': recent[:15],
                        'auto_check_updates': bool(self.auto_check_updates)})
            p.write_text(json.dumps(old), 'utf8')

    def _recent_vaults(self):
        try:
            data = json.loads(recent_state_file().read_text('utf8'))
            return [str(Vault.locate(p)) for p in data.get('recent', [])
                    if Path(p).is_dir() and (Path(p) / 'vault.json').is_file()]
        except (OSError, ValueError):
            return []

    def _error(self, exc):
        messagebox.showerror('VEIL', str(exc), parent=self.root)

    def show_login(self):
        self._clear()
        self.root.configure(bg=BG)
        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill='both', expand=True)
        sidebar = tk.Frame(shell, bg=SIDEBAR, width=330)
        sidebar.pack(side='left', fill='y')
        sidebar.pack_propagate(False)
        mark = tk.Canvas(sidebar, width=95, height=95, bg=SIDEBAR, highlightthickness=0)
        mark.pack(pady=(120, 15))
        mark.create_polygon(47, 9, 83, 47, 47, 84, 11, 47, outline=ACCENT,
                            fill='', width=7, joinstyle='round')
        mark.create_polygon(47, 29, 65, 47, 47, 66, 29, 47, outline='#e0f4ff',
                            fill='', width=3)
        tk.Label(sidebar, text='VEIL', bg=SIDEBAR, fg=FG,
                 font=(FONT, 34, 'bold')).pack()
        tk.Label(sidebar, text='Your files. Your keys. Your privacy.', bg=SIDEBAR,
                 fg=MUTED, font=(FONT, 11), wraplength=260).pack(pady=(8, 12))
        tk.Frame(sidebar, bg=BORDER, height=1).pack(fill='x', padx=36, pady=25)
        tk.Label(sidebar, text='◈  Local encrypted storage\n\n'
                                '◈  Offline password recovery\n\n'
                                '◈  No cloud account required',
                 bg=SIDEBAR, fg='#bdcfe4', font=(FONT, 10),
                 justify='left', wraplength=270).pack(anchor='w', padx=43)
        right = tk.Frame(shell, bg=BG)
        right.pack(side='left', fill='both', expand=True)
        outer = tk.Frame(right, bg=BG)
        outer.pack(expand=True, fill='both', padx=55, pady=55)
        tk.Label(outer, text='Welcome to VEIL', bg=BG, fg=FG,
                 font=(FONT, 26, 'bold')).pack(anchor='w', pady=(20, 7))
        tk.Label(outer, text='Unlock your private encrypted vault', bg=BG, fg=MUTED,
                 font=(FONT, 12)).pack(anchor='w', pady=(0, 25))
        base = ttk.Frame(outer, style='Card.TFrame', padding=28)
        base.pack(fill='x')
        tk.Label(base, text='CURRENT VAULT', bg=PANEL, fg=MUTED,
                 font=(FONT, 9, 'bold')).pack(anchor='w', pady=(0, 7))
        tk.Label(base, text=str(self.folder) if self.folder else 'No vault selected',
                 bg=PANEL, fg=FG, wraplength=550, justify='left',
                 font=(FONT, 11, 'bold')).pack(anchor='w')
        bar = ttk.Frame(base, style='Card.TFrame')
        bar.pack(anchor='w', fill='x', pady=(16, 21))
        ttk.Button(bar, text='Open existing vault', command=self._choose_vault).pack(side='left', padx=(0, 10))
        ttk.Button(bar, text='Create a vault', command=self._create_vault).pack(side='left')
        if self._recent_vaults():
            recent = self._recent_vaults()
            tk.Label(base, text='RECENT VAULTS', bg=PANEL, fg=MUTED,
                     font=(FONT, 9, 'bold')).pack(anchor='w', pady=(0, 6))
            choices = ttk.Combobox(base, state='readonly', values=recent)
            choices.pack(fill='x', pady=(0, 11))
            def choose(_event=None):
                try:
                    self.folder = Vault.locate(choices.get())
                    self._save_recent()
                    self.show_login()
                except Exception as exc:
                    self._error(exc)
            choices.bind('<<ComboboxSelected>>', choose)
        tk.Label(base, text='MASTER PASSWORD', bg=PANEL, fg=MUTED,
                 font=(FONT, 9, 'bold')).pack(anchor='w', pady=(3, 7))
        self.password_var = tk.StringVar()
        self.password_entry = ttk.Entry(base, show='●', textvariable=self.password_var)
        self.password_entry.pack(anchor='w', fill='x', pady=(0, 16), ipady=5)
        self.password_entry.bind('<Return>', lambda e: self._unlock())
        btnrow = ttk.Frame(base, style='Card.TFrame')
        btnrow.pack(fill='x')
        ttk.Button(btnrow, text='🔒  Unlock Vault', style='Accent.TButton',
                   command=self._unlock).pack(side='left', padx=(0, 12))
        ttk.Button(btnrow, text='Use recovery key', command=self._recover).pack(side='left')
        if self.folder:
            try:
                enabled = Vault._load(self.folder).config.get('capture_enabled', False)
            except VaultError:
                enabled = False
            self._note(base, ('◉  Login camera enabled — encrypted snapshot recorded before login.'
                              if enabled else '○  Login camera off — enable it from Settings.'),
                       SUCCESS if enabled else MUTED)
            if enabled and self.folder not in self.captured_this_session:
                self.captured_this_session.add(self.folder)
                self.root.after(900, lambda path=self.folder: self._take_login_snapshot(path))
        else:
            self._note(base, 'Select an existing vault or create a new vault on your chosen drive.')
        self._note(base, 'VEIL encrypts contents and filenames. Encrypted vault files are visible '
                         'to Windows and can still be copied or deleted by anyone with drive access.')
        self.password_entry.focus_set()

    def _take_login_snapshot(self, path: Path):
        # Photo capture is clearly disclosed on the login screen and requires
        # prior opt-in from Settings. Only one snapshot per session per vault.
        try:
            if not Vault._load(path).config.get('capture_enabled', False):
                return
        except VaultError:
            return
        def worker():
            try:
                image = capture_jpeg()
                record_camera_snapshot(path, image)
            except Exception as exc:
                self.root.after(0, lambda msg=str(exc): messagebox.showwarning(
                    'VEIL camera', f'Login snapshot could not be taken:\n{msg}', parent=self.root))
        threading.Thread(target=worker, daemon=True).start()

    def _choose_vault(self):
        folder = filedialog.askdirectory(title='Choose the VEIL vault or its parent folder')
        if not folder:
            return
        try:
            self.folder = Vault.locate(folder)
            self._save_recent()
            self.show_login()
        except VaultError as exc:
            self._error(exc)

    def _create_vault(self):
        parent = filedialog.askdirectory(title='Choose a drive/folder for the NEW encrypted vault')
        if not parent:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('Create a VEIL vault')
        dialog.geometry('580x430')
        dialog.configure(bg=PANEL)
        dialog.transient(self.root)
        dialog.grab_set()
        tk.Label(dialog, text='Create your private vault', font=(FONT, 18, 'bold'),
                 bg=PANEL, fg=FG).pack(anchor='w', padx=26, pady=(23, 8))
        tk.Label(dialog, text=f'Storage: {Path(parent) / "VEIL_Vault"}', bg=PANEL, fg=MUTED,
                 wraplength=510, justify='left').pack(anchor='w', padx=26, pady=4)
        tk.Label(dialog, text='Vault name (optional, letters/numbers)', bg=PANEL, fg=FG).pack(anchor='w', padx=26, pady=(10, 4))
        vault_name = ttk.Entry(dialog, width=46)
        vault_name.pack(anchor='w', padx=26)
        tk.Label(dialog, text='Password (12 characters or more)', bg=PANEL, fg=FG).pack(anchor='w', padx=26, pady=(13, 4))
        first = ttk.Entry(dialog, show='●', width=52)
        first.pack(anchor='w', padx=26)
        tk.Label(dialog, text='Confirm password', bg=PANEL, fg=FG).pack(anchor='w', padx=26, pady=(14, 4))
        second = ttk.Entry(dialog, show='●', width=52)
        second.pack(anchor='w', padx=26)
        def create():
            if first.get() != second.get():
                messagebox.showerror('VEIL', 'Passwords do not match.', parent=dialog)
                return
            try:
                name = vault_name.get().strip()
                if name and not all(c.isalnum() or c in '-_' for c in name):
                    raise VaultError('Vault name can only contain letters, numbers, dashes, and underscores.')
                vault, recovery = Vault.create(parent, first.get(),
                        name='VEIL_Vault_' + name if name else 'VEIL_Vault')
            except Exception as exc:
                messagebox.showerror('VEIL', str(exc), parent=dialog)
                return
            dialog.destroy()
            self.vault = vault
            self.folder = vault.path
            self._save_recent()
            self._show_recovery_key(recovery)
            self.show_vault()
        ttk.Button(dialog, text='Create vault', style='Accent.TButton', command=create).pack(anchor='w', padx=26, pady=24)
        first.focus_set()

    def _show_recovery_key(self, recovery: str):
        window = tk.Toplevel(self.root)
        window.title('SAVE YOUR RECOVERY KEY')
        window.geometry('760x350')
        window.configure(bg=PANEL)
        window.transient(self.root)
        window.grab_set()
        tk.Label(window, text='Save your recovery key now', bg=PANEL, fg=FG,
                 font=(FONT, 19, 'bold')).pack(anchor='w', padx=25, pady=(20, 12))
        tk.Label(window, text=recovery, bg=PANEL2, fg=SUCCESS,
                 font=('Consolas', 14, 'bold'), wraplength=690,
                 padx=12, pady=16).pack(fill='x', padx=25)
        tk.Label(window, text='This key can reset your password. Anyone with this key and your vault '
                              'can unlock it. Store it separately from the vault; losing both the '
                              'password and recovery key means losing access.',
                 bg=PANEL, fg=MUTED, wraplength=690, justify='left').pack(anchor='w', padx=25, pady=13)
        def save():
            output = filedialog.asksaveasfilename(parent=window, title='Save recovery key on ANOTHER drive',
                    initialfile='VEIL-Recovery-Key.txt', defaultextension='.txt')
            if output:
                if self.folder and (Path(output).resolve() == self.folder or self.folder in Path(output).resolve().parents):
                    messagebox.showerror('VEIL', 'Never store the recovery key inside your vault.', parent=window)
                    return
                Path(output).write_text('VEIL recovery key\n\n' + recovery + '\n\nKeep this offline and private.\n', 'utf8')
                messagebox.showinfo('VEIL', 'Recovery key saved. Store another offline copy.', parent=window)
        ttk.Button(window, text='Save recovery key to a file', command=save).pack(anchor='w', padx=25, pady=6)
        confirmed = tk.BooleanVar(value=False)
        ttk.Checkbutton(window, text='I have safely recorded my recovery key',
                        variable=confirmed).pack(anchor='w', padx=25, pady=6)
        def close():
            if not confirmed.get():
                messagebox.showwarning('VEIL', 'Record your recovery key before continuing.', parent=window)
                return
            window.destroy()
        window.protocol('WM_DELETE_WINDOW', close)
        ttk.Button(window, text='Continue', style='Accent.TButton', command=close).pack(anchor='e', padx=25)
        window.wait_window()

    def _unlock(self):
        if not self.folder:
            messagebox.showwarning('VEIL', 'Open or create a vault first.')
            return
        try:
            self.vault = Vault.open_password(self.folder, self.password_var.get())
            try:
                record_login_event(self.folder, True)
            except Exception:
                pass
            self.password_var.set('')
            self.last_activity = time.monotonic()
            self.show_vault()
        except Exception as exc:
            try:
                record_login_event(self.folder, False)
            except Exception:
                pass
            self._error(exc)

    def _recover(self):
        if not self.folder:
            messagebox.showwarning('VEIL', 'Choose a vault first.')
            return
        key = simpledialog.askstring('Recovery key', 'Enter your saved recovery key:', parent=self.root)
        if not key:
            return
        try:
            vault = Vault.open_recovery(self.folder, key)
        except Exception as exc:
            self._error(exc)
            return
        new = simpledialog.askstring('New password', 'Choose a new password (12+ characters):',
                                     show='●', parent=self.root)
        if not new:
            vault.lock()
            return
        try:
            vault.change_password(new)
            vault.lock()
            messagebox.showinfo('Password reset', 'Password changed. Log in with the new password.')
        except Exception as exc:
            self._error(exc)

    def show_vault(self):
        self._clear()
        self.current_folder = ''
        self.selected_id = None
        self.selected_ids = set()
        self.file_preview_photo = None
        self._thumbnail_cache = {}
        self.root.configure(bg=BG)

        # Three-pane shell: navigation | workspace | inspector. No vault format changes.
        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill='both', expand=True)
        self.sidebar = tk.Frame(shell, bg=SIDEBAR, width=216, highlightthickness=1,
                                highlightbackground=BORDER)
        self.sidebar.pack(side='left', fill='y')
        self.sidebar.pack_propagate(False)
        brand = tk.Frame(self.sidebar, bg=SIDEBAR)
        brand.pack(fill='x', pady=(26, 32))
        mark = tk.Canvas(brand, width=64, height=60, bg=SIDEBAR, highlightthickness=0)
        mark.pack()
        mark.create_polygon(32, 10, 52, 30, 32, 50, 12, 30, outline='#64b6ff',
                            fill='', width=5, joinstyle='round')
        mark.create_polygon(32, 21, 41, 30, 32, 39, 23, 30, outline='#d0eaff',
                            fill='', width=2, joinstyle='round')
        tk.Label(brand, text='VEIL', bg=SIDEBAR, fg=FG,
                 font=(FONT, 23, 'bold')).pack(pady=(0, 0))

        self.nav_buttons = []
        for i, (glyph, text) in enumerate((('▱', 'Files'), ('▦', 'Media Gallery'), ('♻', 'Recycle Bin'), ('◎', 'Security Photos'), ('◇', 'Activity'), ('⚙', 'Settings'))):
            button = tk.Button(self.sidebar, text=f'   {glyph}    {text}', bg=SIDEBAR, fg='#cfdeef',
                               activebackground=ACTIVE, activeforeground=FG, relief='flat', bd=0,
                               highlightthickness=0, anchor='w', font=(FONT, 11), padx=8, pady=14, cursor='hand2',
                               command=lambda idx=i: self._select_nav(idx))
            button.pack(fill='x', padx=15, pady=4)
            self.nav_buttons.append(button)
        footer = tk.Frame(self.sidebar, bg=SIDEBAR)
        footer.pack(side='bottom', fill='x', padx=20, pady=(15, 23))
        tk.Frame(footer, bg=BORDER, height=1).pack(fill='x', pady=(0, 15))
        tk.Label(footer, text='◈  Vault Unlocked', bg=SIDEBAR, fg=SUCCESS,
                 anchor='w', font=(FONT, 10, 'bold')).pack(fill='x')
        tk.Label(footer, text=str(self.folder), bg=SIDEBAR, fg=MUTED,
                 anchor='w', wraplength=170, justify='left', font=(FONT, 9)).pack(fill='x', pady=(5, 0))

        workspace = tk.Frame(shell, bg=BG)
        workspace.pack(side='left', fill='both', expand=True)
        heading = tk.Frame(workspace, bg=BG)
        heading.pack(fill='x', padx=24, pady=(22, 9))
        title_column = tk.Frame(heading, bg=BG)
        title_column.pack(side='left')
        tk.Label(title_column, text='VEIL', bg=BG, fg=FG,
                 font=(FONT, 25, 'bold')).pack(anchor='w')
        tk.Label(title_column, text='Private encrypted vault', bg=BG, fg=MUTED,
                 font=(FONT, 10)).pack(anchor='w', pady=(0, 2))
        ttk.Button(heading, text='♙  Lock Vault', style='Accent.TButton',
                   command=self.lock).pack(side='right', padx=(13, 0))
        meter = tk.Frame(heading, bg=BG)
        meter.pack(side='right', padx=(10, 0))
        self.space_info = tk.Label(meter, bg=BG, fg='#d9e7f5',
                                   font=(FONT, 9), justify='right')
        self.space_info.pack(anchor='e')
        self.storage_bar = tk.Canvas(meter, width=197, height=7, bg=BG, highlightthickness=0)
        self.storage_bar.pack(anchor='e', pady=(6, 0))
        self._update_space_info()
        search_outer = tk.Frame(heading, bg=PANEL2, highlightthickness=1, highlightbackground=BORDER)
        search_outer.pack(side='right', fill='x', expand=True, padx=(24, 17), pady=(7, 6))
        tk.Label(search_outer, text='⌕', bg=PANEL2, fg='#bdd4ec',
                 font=(FONT, 19)).pack(side='left', padx=(12, 0))
        self.search_box = ttk.Entry(search_outer, textvariable=self.search_var,
                                     style='TEntry', width=24)
        self.search_box.pack(side='left', fill='x', expand=True, padx=(2, 6), ipady=2)
        self.search_box.bind('<Escape>', lambda e: self.search_var.set(''))
        self.search_box.bind('<FocusIn>', lambda e: self._set_search_hint(False))
        self.search_box.bind('<FocusOut>', lambda e: self._set_search_hint(True))
        self.search_hint = tk.Label(search_outer, text='Search vault (Ctrl+F)',
                                    bg=PANEL2, fg=MUTED, cursor='xterm',
                                    font=(FONT, 10))
        self.search_hint.place(x=43, rely=0.5, anchor='w')
        self.search_hint.bind('<Button-1>', lambda e: self.search_box.focus_set())
        self.root.bind('<Control-f>', self._focus_search, add='+')

        self.notebook = ttk.Notebook(workspace, style='Hidden.TNotebook', takefocus=False)
        self.notebook.pack(fill='both', expand=True, padx=(22, 23), pady=(0, 18))
        files = ttk.Frame(self.notebook)
        gallery = ttk.Frame(self.notebook, padding=(7, 10))
        trash = ttk.Frame(self.notebook, padding=(7, 10))
        security = ttk.Frame(self.notebook, padding=(7, 10))
        activity = ttk.Frame(self.notebook, padding=(7, 10))
        settings = ttk.Frame(self.notebook, padding=(7, 10))
        for tab, title in ((files, 'Files'), (gallery, 'Media Gallery'), (trash, 'Recycle Bin'),
                           (security, 'Security photos'), (activity, 'Activity'), (settings, 'Settings')):
            self.notebook.add(tab, text=title)
        self._files_tab(files)
        self._gallery_tab(gallery)
        self._trash_tab(trash)
        self._security_tab(security)
        self._activity_tab(activity)
        self._settings_tab(settings)
        self.root.bind('<Control-a>', self._select_all, add='+')
        self.root.bind('<Escape>', self._clear_selection, add='+')
        self.root.bind('<Delete>', self._delete_key, add='+')
        self.notebook.bind('<<NotebookTabChanged>>', self._sync_nav)
        self._select_nav(0)
        if not getattr(self, '_search_trace_bound', False):
            self.search_var.trace_add('write', self._search_changed)
            self._search_trace_bound = True

    def _select_nav(self, index):
        self.notebook.select(index)
        self._sync_nav()

    def _sync_nav(self, _event=None):
        index = self.notebook.index(self.notebook.select())
        self._active_nav = index
        for i, button in enumerate(self.nav_buttons):
            button.configure(bg=ACTIVE if i == index else SIDEBAR,
                             fg=FG if i == index else '#c5d3e5',
                             font=(FONT, 11, 'bold' if i == index else 'normal'))
        if index == 0:
            self.search_box.configure(state='normal')
        else:
            self.search_box.configure(state='disabled')
        if index == 1:
            self._refresh_gallery()
        elif index == 2:
            self._refresh_trash()
        elif index == 4:
            self._refresh_activity()

    def _focus_search(self, _event=None):
        if self.vault:
            self._select_nav(0)
            self.search_box.focus_set()
            self.search_box.selection_range(0, 'end')
            return 'break'

    def _set_search_hint(self, show=True):
        if not getattr(self, 'search_hint', None) or not self.search_hint.winfo_exists():
            return
        if show and not self.search_var.get():
            self.search_hint.place(x=43, rely=0.5, anchor='w')
        else:
            self.search_hint.place_forget()

    def _search_changed(self, *_args):
        self._set_search_hint(not self.search_box.focus_get() == self.search_box)
        if self.vault and hasattr(self, 'file_host') and self.file_host.winfo_exists():
            self._refresh_files()

    def _update_space_info(self):
        try:
            logical = sum(entry['size'] for entry in self.vault.list_files())
            drive = shutil.disk_usage(self.vault.path)
            self.space_info.configure(text=f'{self._size_label(logical)} in vault  •  '
                                           f'{self._size_label(drive.free)} free')
            self.storage_bar.delete('all')
            self.storage_bar.create_rectangle(0, 0, 197, 7, fill='#25354b', outline='')
            ratio = min(1.0, max(0.0, (drive.used / drive.total) if drive.total else 0.0))
            self.storage_bar.create_rectangle(0, 0, max(3, int(197 * ratio)), 7,
                                               fill=ACCENT, outline='')
        except (OSError, ValueError):
            self.space_info.configure(text='Encrypted vault')


    # --- Private file manager ---

    def _files_tab(self, parent):
        # Breadcrumb / history, then a tight Explorer-style action toolbar.
        top = tk.Frame(parent, bg=BG)
        top.pack(fill='x', pady=(6, 13))
        self.up_button = ttk.Button(top, text='←', command=self._go_up, width=3)
        self.up_button.pack(side='left', padx=(0, 10))
        self.breadcrumb = tk.Label(top, text='Vault', bg=BG, fg='#b7dcff',
                                   anchor='w', font=(FONT, 11, 'bold'))
        self.breadcrumb.pack(side='left', fill='x', expand=True)
        tk.Label(top, text='View', bg=BG, fg=MUTED, font=(FONT, 10)).pack(side='left', padx=(8, 7))
        self.view_var = tk.StringVar(value=self.view_mode)
        self.view_selector = ttk.Combobox(top, state='readonly', width=15,
               values=('Details', 'List', 'Small icons', 'Medium icons',
                       'Large icons', 'Extra large icons', 'Tiles'), textvariable=self.view_var)
        self.view_selector.pack(side='right')
        self.view_selector.bind('<<ComboboxSelected>>', self._on_view_change)

        toolbar = ttk.Frame(parent)
        toolbar.pack(fill='x', pady=(0, 13))
        for title, command, style in [('⊞  New Folder', self._new_folder, 'TButton'),
                                       ('⇧  Add Files', self._add_files, 'TButton'),
                                       ('▣  Add ZIP', self._add_zip, 'TButton'),
                                       ('⇄  Move', self._move_selected, 'TButton'),
                                       ('↓  Export', self._export_file, 'TButton'),
                                       ('✕  Delete', self._delete_file, 'Danger.TButton')]:
            ttk.Button(toolbar, text=title, command=command, style=style).pack(side='left', padx=(0, 7))
        more = tk.Menubutton(toolbar, text='⋯  More ▾', bg=PANEL2, fg=FG,
                             activebackground=ACTIVE, activeforeground=FG, relief='flat',
                             bd=0, padx=13, pady=11, font=(FONT, 10), cursor='hand2')
        menu = tk.Menu(more, tearoff=0, bg=PANEL2, fg=FG, activebackground=ACTIVE, activeforeground=FG)
        for title, action in [('Import folder', self._add_folder),
                              ('Rename selected', self._rename_selected),
                              ('Move to folder', self._move_selected),
                              ('Preview / Browse ZIP', self._preview_file)]:
            menu.add_command(label=title, command=action)
        more.configure(menu=menu)
        more.pack(side='left', padx=(0, 6))

        content = tk.Frame(parent, bg=BG)
        content.pack(fill='both', expand=True)
        left = tk.Frame(content, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        left.pack(side='left', fill='both', expand=True, padx=(0, 14))
        self.file_host = ttk.Frame(left, style='Card.TFrame')
        self.file_host.pack(fill='both', expand=True, padx=1, pady=1)
        self.preview_panel = tk.Frame(content, bg=PANEL, width=288,
                                      highlightthickness=1, highlightbackground=BORDER)
        self.preview_panel.pack(side='right', fill='y')
        self.preview_panel.pack_propagate(False)
        self._register_drop(self.file_host)
        self._register_drop(left)
        self._register_drop(parent)
        self.file_status = tk.Label(parent, text='', bg=BG, fg=MUTED, anchor='w',
                                    font=(FONT, 9))
        self.file_status.pack(fill='x', pady=(10, 0))
        self._refresh_files()

    def _clear_file_preview(self):
        self.file_preview_photo = None
        for child in self.preview_panel.winfo_children():
            child.destroy()

    def _render_file_preview(self):
        if not hasattr(self, 'preview_panel') or not self.preview_panel.winfo_exists():
            return
        self._clear_file_preview()
        parent = self.preview_panel
        item = getattr(self, 'visible_entries', {}).get(self.selected_id)
        if len(self.selected_ids) > 1:
            tk.Label(parent, text=f'{len(self.selected_ids)} items selected', bg=PANEL, fg=FG,
                     font=(FONT, 15, 'bold')).pack(anchor='w', padx=17, pady=20)
            tk.Label(parent, text='Use Move or Delete to perform a batch action.', bg=PANEL,
                     fg=MUTED, wraplength=245).pack(anchor='w', padx=17)
            return
        if not item:
            empty = tk.Frame(parent, bg=PANEL)
            empty.pack(fill='both', expand=True, padx=17, pady=20)
            tk.Label(empty, text='FILE DETAILS', bg=PANEL, fg=MUTED,
                     font=(FONT, 10, 'bold')).pack(anchor='w', pady=(2, 26))
            tk.Label(empty, text='◇', bg=PANEL, fg='#5b8ab9',
                     font=(FONT, 51)).pack(pady=(30, 6))
            tk.Label(empty, text='Select a file to preview', bg=PANEL, fg=FG,
                     font=(FONT, 12, 'bold')).pack()
            tk.Label(empty, text='Images and supported videos can be viewed securely inside VEIL.',
                     bg=PANEL, fg=MUTED, wraplength=240, justify='center',
                     font=(FONT, 10)).pack(pady=12)
            return
        tk.Label(parent, text=item['name'], bg=PANEL, fg=FG, anchor='w',
                 wraplength=252, justify='left', font=(FONT, 12, 'bold')).pack(fill='x', padx=17, pady=(18, 3))
        kind = 'Folder' if item['kind'] == 'folder' else (Path(item['name']).suffix[1:].upper() or 'File')
        tk.Label(parent, text=kind, bg=PANEL, fg=MUTED, anchor='w',
                 font=(FONT, 9)).pack(fill='x', padx=17)
        preview = tk.Frame(parent, bg='#162940', height=205,
                            highlightthickness=1, highlightbackground=BORDER)
        preview.pack(fill='x', padx=12, pady=(20, 14))
        preview.pack_propagate(False)
        photo = None
        if item['kind'] == 'file' and Path(item['name']).suffix.casefold() in (
                '.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'):
            photo = self._thumbnail(item, 235)
        elif item['kind'] == 'file' and Path(item['name']).suffix.casefold() in VIDEO_EXTENSIONS:
            photo = self._video_thumbnail(item)
        if photo:
            self.file_preview_photo = photo
            label = tk.Label(preview, image=photo, bg='#162940', cursor='hand2')
            label.image = photo
            label.pack(fill='both', expand=True)
            label.bind('<Double-1>', lambda e: self._preview_file())
        else:
            glyph = self._file_glyph(item['name'], item['kind'])
            tk.Label(preview, text=glyph, bg='#162940', fg=ACCENT,
                     font=(FONT, 51)).pack(expand=True)
        if item['kind'] == 'file':
            action = 'Browse ZIP' if item['name'].lower().endswith('.zip') else '▶  Preview'
            ttk.Button(parent, text=action, command=self._preview_file,
                       style='Accent.TButton').pack(fill='x', padx=13, pady=(0, 15))
        tk.Label(parent, text='Details', bg=PANEL, fg=FG, anchor='w',
                 font=(FONT, 12, 'bold')).pack(fill='x', padx=16, pady=(7, 12))
        for label, value in [('Type', kind),
                             ('Size', self._size_label(item['size']) if item['size'] is not None else '—'),
                             ('Imported (UTC)', item['added'].replace('+00:00', 'Z') if item['added'] else '—')]:
            line = tk.Frame(parent, bg=PANEL)
            line.pack(fill='x', padx=15, pady=(0, 6))
            tk.Label(line, text=label, bg=PANEL, fg=MUTED,
                     font=(FONT, 9)).pack(anchor='w')
            tk.Label(line, text=value, bg=PANEL, fg=FG, wraplength=248,
                     justify='left', font=(FONT, 9)).pack(anchor='w', pady=(2, 2))
            tk.Frame(parent, bg=BORDER, height=1).pack(fill='x', padx=15, pady=(0, 6))

    def _video_thumbnail(self, item):
        # Optional best-effort decoder for small video files. No temporary MP4 created.
        if item['size'] > 24 * 1024 * 1024 or not video_available():
            return None
        cache_key = ('video', item['id'])
        if cache_key in self._thumbnail_cache:
            return self._thumbnail_cache[cache_key]
        try:
            import av
            from PIL import ImageTk
            raw = self.vault.read_small_file(item['id'], limit=24*1024*1024)
            with av.open(io.BytesIO(raw), 'r') as video:
                frame = next(video.decode(video=0))
                image = frame.to_image()
                image.thumbnail((262, 196))
                photo = ImageTk.PhotoImage(image)
            self._thumbnail_cache[cache_key] = photo
            return photo
        except Exception:
            return None


    def _register_drop(self, widget, destination=None):
        if not DND_FILES or not hasattr(widget, 'drop_target_register'):
            return
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind('<<Drop>>', lambda event, dest=destination: self._handle_drop(event, dest))

    def _refresh_files(self):
        if not self.vault:
            return
        for widget in self.file_host.winfo_children():
            widget.destroy()
        self._tile_widgets = {}
        all_folders = self.vault.list_folders()
        query = self.search_var.get().strip().casefold()
        entries = []
        for folder in all_folders:
            parent = folder.rsplit('/', 1)[0] if '/' in folder else ''
            if (parent == self.current_folder and not query) or (query and query in folder.casefold()):
                entries.append({'kind': 'folder', 'id': 'dir:' + folder,
                                'path': folder, 'name': folder.rsplit('/', 1)[-1],
                                'size': None, 'added': ''})
        for entry in self.vault.list_files():
            parent = entry['path'].rsplit('/', 1)[0] if '/' in entry['path'] else ''
            if (parent == self.current_folder and not query) or (query and query in entry['path'].casefold()):
                entries.append({'kind': 'file', 'id': entry['file_id'], 'path': entry['path'],
                                'name': entry['path'].rsplit('/', 1)[-1],
                                'size': entry['size'], 'added': entry['imported_at']})
        entries.sort(key=lambda item: (item['kind'] != 'folder', item['name'].casefold()))
        self.visible_entries = {item['id']: item for item in entries}
        self.breadcrumb.configure(text='Vault  ›  ' + (self.current_folder.replace('/', '  ›  ')
                                                   if self.current_folder else 'My files') +
                                  ('   •   Search results' if query else ''))
        self.up_button.configure(state=('normal' if self.current_folder and not query else 'disabled'))
        self.selected_ids.intersection_update(self.visible_entries.keys())
        if self.selected_id not in self.visible_entries:
            self.selected_id = next(iter(self.selected_ids), None)
        if self.view_mode in ('Details', 'List'):
            self._render_tree(entries)
        else:
            self._render_icons(entries)
        if not self.busy:
            self.file_status.configure(text=f'{len(entries)} item(s)  •  '
                                             'Drop files or folders here to encrypt  •  '
                                             'Source files remain unchanged.', fg=MUTED)
        self._render_file_preview()
        self._update_space_info()


    @staticmethod
    def _size_label(value):
        if value is None:
            return ''
        if value >= 1024 ** 3:
            return f'{value / 1024**3:.2f} GB'
        if value >= 1024 ** 2:
            return f'{value / 1024**2:.2f} MB'
        return f'{value / 1024:.1f} KB'

    @staticmethod
    def _file_glyph(name, kind):
        if kind == 'folder':
            return '📁'
        suffix = Path(name).suffix.lower()
        if suffix in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'):
            return '🖼'
        if suffix in ('.mp4', '.mkv', '.avi', '.mov', '.webm'):
            return '🎞'
        if suffix in ('.mp3', '.wav', '.flac', '.ogg'):
            return '♫'
        if suffix in ('.zip', '.7z', '.rar'):
            return '▣'
        return '▤'

    def _tree_icon(self, item):
        from PIL import Image, ImageDraw, ImageTk
        suffix = Path(item['name']).suffix.casefold()
        category = ('folder' if item['kind'] == 'folder' else
                    'image' if suffix in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp') else
                    'video' if suffix in VIDEO_EXTENSIONS else
                    'zip' if suffix == '.zip' else 'file')
        if category in self._tree_icon_cache:
            return self._tree_icon_cache[category]
        image = Image.new('RGBA', (23, 23), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        if category == 'folder':
            draw.rounded_rectangle((2, 7, 21, 19), radius=2, fill='#e9aa3d')
            draw.rounded_rectangle((3, 4, 12, 10), radius=2, fill='#f5c363')
        elif category == 'image':
            draw.rounded_rectangle((3, 3, 20, 20), radius=2, outline='#9adcf2', width=2)
            draw.ellipse((13, 6, 17, 10), fill='#9adcf2')
            draw.line(((5, 17), (10, 12), (13, 15), (16, 12), (19, 17)), fill='#9adcf2', width=2)
        else:
            stroke = '#9fd3fa'
            draw.rounded_rectangle((5, 2, 19, 21), radius=2, outline=stroke, width=2)
            draw.line(((9, 8), (15, 8)), fill=stroke, width=2)
            if category == 'video':
                draw.polygon(((10, 11), (10, 18), (16, 14)), fill=stroke)
            elif category == 'zip':
                draw.rectangle((10, 11, 13, 17), outline=stroke, width=1)
        asset = ImageTk.PhotoImage(image, master=self.root)
        self._tree_icon_cache[category] = asset
        return asset

    def _render_tree(self, entries):
        details = self.view_mode == 'Details'
        container = ttk.Frame(self.file_host, style='Card.TFrame')
        container.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(container, columns=('size', 'type', 'added'),
                                  show=('tree headings' if details else 'tree'), selectmode='extended')
        self.tree.heading('#0', text='Name')
        self.tree.heading('size', text='Size')
        self.tree.heading('type', text='Type')
        self.tree.heading('added', text='Imported (UTC)')
        self.tree.column('#0', width=(300 if details else 690), minwidth=140)
        self.tree.column('size', width=105, minwidth=85)
        self.tree.column('type', width=112, minwidth=94)
        self.tree.column('added', width=190, minwidth=130)
        bar = DarkScrollbar(container, command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')
        for i, item in enumerate(entries):
            suffix = Path(item['name']).suffix.lstrip('.').upper() if item['kind'] == 'file' else 'Folder'
            typ = f'{suffix} file' if suffix != 'Folder' else 'File folder'
            self.tree.insert('', 'end', iid=item['id'],
                             text='  ' + item['name'], image=self._tree_icon(item),
                             values=(self._size_label(item['size']), typ,
                                     item['added'].replace('+00:00', 'Z')),
                             tags=('stripe' if i % 2 else 'plain',))
        self.tree.tag_configure('stripe', background='#142238')
        self.tree.tag_configure('plain', background=PANEL)
        if self.selected_ids:
            self.tree.selection_set(tuple(self.selected_ids))
        self.tree.bind('<<TreeviewSelect>>', self._tree_select)
        self.tree.bind('<Double-1>', lambda event: self._open_selected())
        self.tree.bind('<Return>', lambda event: self._open_selected())
        self.tree.bind('<Button-3>', self._show_context_menu)
        if DND_FILES and hasattr(self.tree, 'drop_target_register'):
            self.tree.drop_target_register(DND_FILES)
            self.tree.dnd_bind('<<Drop>>', self._drop_on_tree)


    def _render_icons(self, entries):
        container = ttk.Frame(self.file_host)
        container.pack(fill='both', expand=True)
        self.tile_canvas = tk.Canvas(container, bg=PANEL, highlightthickness=0)
        scrollbar = DarkScrollbar(container, command=self.tile_canvas.yview)
        self.tile_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.tile_canvas.pack(side='left', fill='both', expand=True)
        self.tile_area = tk.Frame(self.tile_canvas, bg=PANEL)
        window_id = self.tile_canvas.create_window((0, 0), window=self.tile_area, anchor='nw')
        self.tile_area.bind('<Configure>', lambda e: self.tile_canvas.configure(
            scrollregion=self.tile_canvas.bbox('all')))
        self.tile_canvas.bind('<Configure>', lambda e: self._reflow_tiles(e.width, window_id))
        self.tile_canvas.bind('<MouseWheel>', lambda e: self.tile_canvas.yview_scroll(
            -int(e.delta / 120) if e.delta else 0, 'units'))
        self._register_drop(self.tile_canvas)
        self._register_drop(self.tile_area)
        self._create_tiles(entries)
        self.tile_canvas.bind('<ButtonPress-1>', self._marquee_press)
        self.tile_canvas.bind('<B1-Motion>', self._marquee_move)
        self.tile_canvas.bind('<ButtonRelease-1>', self._marquee_release)
        self._marquee_id = None
        self.root.after_idle(lambda: self._reflow_tiles(self.tile_canvas.winfo_width(), window_id)
                             if self.vault and self.tile_canvas.winfo_exists() else None)

    def _create_tiles(self, entries):
        tile_sizes = {'Small icons': (200, 52, 24), 'Medium icons': (150, 135, 48),
                      'Large icons': (178, 166, 84), 'Extra large icons': (206, 205, 116),
                      'Tiles': (250, 85, 46)}
        self.tile_width, self.tile_height, icon_size = tile_sizes[self.view_mode]
        self.tile_order = []
        for item in entries:
            fid = item['id']
            frame = tk.Frame(self.tile_area, bg=PANEL2 if fid in self.selected_ids else PANEL,
                             highlightthickness=1, highlightbackground=ACCENT if fid in self.selected_ids else PANEL2,
                             width=self.tile_width - 8, height=self.tile_height - 8)
            frame.grid_propagate(False)
            self.tile_order.append(frame)
            self._tile_widgets[fid] = frame
            horizontal = self.view_mode in ('Small icons', 'Tiles')
            if horizontal:
                icon_holder = tk.Frame(frame, bg=frame['bg'], width=icon_size + 14, height=icon_size + 12)
                icon_holder.pack(side='left', padx=7)
                icon_holder.pack_propagate(False)
                desc = tk.Frame(frame, bg=frame['bg'])
                desc.pack(side='left', fill='both', expand=True)
            else:
                icon_holder = tk.Frame(frame, bg=frame['bg'], height=icon_size + 8)
                icon_holder.pack(side='top', fill='x', pady=(7, 2))
                desc = tk.Frame(frame, bg=frame['bg'])
                desc.pack(side='top', fill='x', expand=True)
            photo = self._thumbnail(item, icon_size)
            if photo:
                icon = tk.Label(icon_holder, image=photo, bg=frame['bg'])
                icon.image = photo
            else:
                icon = tk.Label(icon_holder, text=self._file_glyph(item['name'], item['kind']),
                                bg=frame['bg'], fg=ACCENT, font=(FONT, int(max(15, min(icon_size * .64, 58)))))
            icon.pack(expand=True)
            name = tk.Label(desc, text=item['name'], bg=frame['bg'], fg=FG,
                            wraplength=(self.tile_width - (74 if horizontal else 24)),
                            justify=('left' if horizontal else 'center'),
                            font=(FONT, 9 if self.view_mode == 'Small icons' else 10))
            name.pack(fill='x', padx=3)
            if self.view_mode == 'Tiles' and item['size'] is not None:
                tk.Label(desc, text=self._size_label(item['size']), bg=frame['bg'], fg=MUTED,
                         font=(FONT, 8)).pack(anchor='w')
            for widget in (frame, icon_holder, icon, desc, name):
                widget.bind('<Button-1>', lambda e, ident=fid: self._select_tile(ident, e))
                widget.bind('<B1-Motion>', lambda e, ident=fid: self._tile_drag_select(e, ident))
                widget.bind('<ButtonRelease-1>', self._marquee_release)
                widget.bind('<Double-Button-1>', lambda e, ident=fid: self._open_entry(ident))
                widget.bind('<Button-3>', lambda e, ident=fid: self._show_context_menu(e, ident))
                widget.bind('<MouseWheel>', lambda e: self.tile_canvas.yview_scroll(
                    -int(e.delta / 120) if e.delta else 0, 'units'))
            if item['kind'] == 'folder':
                self._register_drop(frame, item['path'])
                self._register_drop(icon, item['path'])
                self._register_drop(name, item['path'])
            else:
                self._register_drop(frame)
                self._register_drop(icon)
                self._register_drop(name)

    def _reflow_tiles(self, width, window_id):
        if not getattr(self, 'tile_order', None):
            return
        columns = max(1, width // self.tile_width)
        for i, widget in enumerate(self.tile_order):
            widget.grid(row=i // columns, column=i % columns, padx=4, pady=4, sticky='nw')
        self.tile_canvas.itemconfigure(window_id, width=max(0, width - 2))

    def _thumbnail(self, item, size):
        if item['kind'] != 'file' or item['size'] > 8 * 1024 * 1024:
            return None
        if Path(item['name']).suffix.casefold() not in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'):
            return None
        if len(self._thumbnail_cache) >= 160 and (item['id'], size) not in self._thumbnail_cache:
            return None
        cache_key = item['id'], size
        if cache_key in self._thumbnail_cache:
            return self._thumbnail_cache[cache_key]
        try:
            from PIL import Image, ImageTk
            raw = self.vault.read_small_file(item['id'], limit=8*1024*1024)
            with Image.open(io.BytesIO(raw)) as original:
                original.thumbnail((size, size))
                thumb = ImageTk.PhotoImage(original.copy())
            self._thumbnail_cache[cache_key] = thumb
            return thumb
        except Exception:
            return None

    def _on_view_change(self, _event=None):
        self.view_mode = self.view_var.get()
        self._save_recent()
        self._refresh_files()

    def _tree_select(self, _event=None):
        if getattr(self, 'tree', None) and self.tree.winfo_exists():
            self.selected_ids = set(self.tree.selection())
            self.selected_id = next(iter(self.selected_ids), None) if len(self.selected_ids) == 1 else None
            self._render_file_preview()
            self._update_selection_status()

    def _paint_tiles(self):
        for ident, frame in self._tile_widgets.items():
            if frame.winfo_exists():
                frame.configure(bg=PANEL2 if ident in self.selected_ids else PANEL,
                                highlightbackground=ACCENT if ident in self.selected_ids else PANEL2)

    def _select_tile(self, fid, event=None):
        if event and event.state & 0x0004:  # Ctrl toggle
            if fid in self.selected_ids:
                self.selected_ids.discard(fid)
            else:
                self.selected_ids.add(fid)
        elif event and event.state & 0x0001 and self._selection_anchor in self.visible_entries:
            keys = list(self.visible_entries)
            lo, hi = sorted((keys.index(self._selection_anchor), keys.index(fid)))
            self.selected_ids.update(keys[lo:hi + 1])
        else:
            self.selected_ids = {fid}
        self._selection_anchor = fid
        self.selected_id = next(iter(self.selected_ids), None) if len(self.selected_ids) == 1 else None
        self._paint_tiles()
        self._render_file_preview()
        self._update_selection_status()
        if event:
            self._drag_origin = (event.x_root, event.y_root)
            self._drag_base = set(self.selected_ids) if event.state & 0x0004 else set()

    def _marquee_press(self, event):
        self._drag_origin = (event.x_root, event.y_root)
        self._drag_base = set(self.selected_ids) if event.state & 0x0004 else set()
        if not (event.state & 0x0004):
            self.selected_ids.clear()
            self.selected_id = None
            self._paint_tiles()
            self._render_file_preview()

    def _tile_drag_select(self, event, fid):
        if not hasattr(self, '_drag_origin'):
            self._drag_origin = (event.x_root, event.y_root)
            self._drag_base = set()
        self._marquee_root(event.x_root, event.y_root)

    def _marquee_move(self, event):
        self._marquee_root(event.x_root, event.y_root)

    def _marquee_root(self, xroot, yroot):
        if not hasattr(self, '_drag_origin') or not getattr(self, 'tile_canvas', None):
            return
        x0, y0 = self._drag_origin
        if abs(xroot - x0) < 6 and abs(yroot - y0) < 6:
            return
        self._marquee_active = True
        selected = set(getattr(self, '_drag_base', set()))
        a, b, c, d = min(x0, xroot), min(y0, yroot), max(x0, xroot), max(y0, yroot)
        for ident, frame in self._tile_widgets.items():
            if not frame.winfo_exists():
                continue
            fx, fy = frame.winfo_rootx(), frame.winfo_rooty()
            if fx < c and fy < d and fx + frame.winfo_width() > a and fy + frame.winfo_height() > b:
                selected.add(ident)
        self.selected_ids = selected
        self.selected_id = next(iter(selected), None) if len(selected) == 1 else None
        self._paint_tiles()
        self._update_selection_status()
        # Draw a selection outline on the canvas for empty-space drag.
        canvas = self.tile_canvas
        x1, y1 = canvas.canvasx(x0 - canvas.winfo_rootx()), canvas.canvasy(y0 - canvas.winfo_rooty())
        x2, y2 = canvas.canvasx(xroot - canvas.winfo_rootx()), canvas.canvasy(yroot - canvas.winfo_rooty())
        if getattr(self, '_marquee_id', None):
            canvas.delete(self._marquee_id)
        self._marquee_id = canvas.create_rectangle(x1, y1, x2, y2, outline=ACCENT, width=2, dash=(5, 3))

    def _marquee_release(self, _event=None):
        if getattr(self, '_marquee_id', None) and self.tile_canvas.winfo_exists():
            self.tile_canvas.delete(self._marquee_id)
        self._marquee_id = None
        self._drag_origin = None
        if getattr(self, '_marquee_active', False):
            self._marquee_active = False
            self._render_file_preview()

    def _update_selection_status(self):
        if self.selected_ids and hasattr(self, 'file_status'):
            self.file_status.configure(text=f'{len(self.selected_ids)} selected • Ctrl/Shift to add • Move or Delete for batch actions')

    def _select_all(self, _event=None):
        if not self.vault or self._active_nav != 0 or not self.visible_entries:
            return
        if self.root.focus_get() == self.search_box:
            return
        self.selected_ids = set(self.visible_entries)
        self.selected_id = next(iter(self.selected_ids), None) if len(self.selected_ids) == 1 else None
        if self.view_mode in ('Details', 'List'):
            self.tree.selection_set(tuple(self.selected_ids))
        else:
            self._paint_tiles()
            self._render_file_preview()
        self._update_selection_status()
        return 'break'

    def _clear_selection(self, _event=None):
        if not self.vault or self._active_nav != 0:
            return
        self.selected_ids = set()
        self.selected_id = None
        if self.view_mode in ('Details', 'List'):
            self.tree.selection_remove(self.tree.selection())
        else:
            self._paint_tiles()
        self._render_file_preview()

    def _delete_key(self, _event=None):
        if self.vault and self._active_nav == 0 and self.selected_ids:
            self._delete_file()
            return 'break'

    def _selection(self):
        if len(self.selected_ids) != 1:
            messagebox.showwarning('VEIL', 'Select exactly one file or folder for this action.', parent=self.root)
            return None
        selected = next(iter(self.selected_ids))
        if selected not in getattr(self, 'visible_entries', {}):
            messagebox.showwarning('VEIL', 'Select a file or folder first.', parent=self.root)
            return None
        return self.visible_entries[selected]

    def _selected_file(self):
        item = self._selection()
        if not item:
            return None
        if item['kind'] != 'file':
            messagebox.showinfo('VEIL', 'Select a file, not a folder.', parent=self.root)
            return None
        return item['id']

    def _go_up(self):
        if self.current_folder:
            self.current_folder = self.current_folder.rsplit('/', 1)[0] if '/' in self.current_folder else ''
            self.selected_id = None
            self._refresh_files()

    def _open_entry(self, fid):
        item = self.visible_entries.get(fid)
        if not item:
            return
        self.selected_id = fid
        self.selected_ids = {fid}
        if item['kind'] == 'folder':
            self.current_folder = item['path']
            self.selected_id = None
            self.selected_ids = set()
            self._refresh_files()
        else:
            if item['name'].casefold().endswith('.zip'):
                self._open_zip(item['id'])
            else:
                self._preview_file()

    def _open_selected(self):
        if self.selected_id:
            self._open_entry(self.selected_id)

    def _show_context_menu(self, event, fid=None):
        if fid is None and self.view_mode in ('Details', 'List'):
            fid = self.tree.identify_row(event.y)
            if fid and fid not in self.selected_ids:
                self.tree.selection_set(fid)
        if not fid or fid not in self.visible_entries:
            return
        if fid not in self.selected_ids:
            self.selected_ids = {fid}
        self.selected_id = fid if len(self.selected_ids) == 1 else None
        menu = tk.Menu(self.root, tearoff=False, bg=PANEL2, fg=FG,
                       activebackground='#31577b', activeforeground=FG)
        item = self.visible_entries[fid]
        menu.add_command(label='Open' if item['kind'] == 'folder' else
                         ('Browse ZIP' if item['name'].casefold().endswith('.zip') else 'Preview'),
                         command=self._open_selected)
        menu.add_separator()
        if len(self.selected_ids) == 1:
            menu.add_command(label='Rename', command=self._rename_selected)
        menu.add_command(label='Move selected to folder…', command=self._move_selected)
        menu.add_command(label='Export' if item['kind'] == 'file' else 'Export folder…',
                         command=self._export_file)
        menu.add_separator()
        menu.add_command(label='Delete', command=self._delete_file)
        menu.tk_popup(event.x_root, event.y_root)
        menu.grab_release()

    def _new_folder(self):
        if self.busy:
            return
        name = simpledialog.askstring('New private folder', 'Folder name:', parent=self.root)
        if not name:
            return
        path = self.current_folder + '/' + name if self.current_folder else name
        try:
            actual = self.vault.create_folder(path)
            self.selected_id = 'dir:' + actual
            self._refresh_files()
        except Exception as exc:
            self._error(exc)

    def _rename_selected(self):
        if self.busy:
            return
        item = self._selection()
        if not item:
            return
        name = simpledialog.askstring('Rename', 'New name:', initialvalue=item['name'], parent=self.root)
        if not name or name == item['name']:
            return
        parent = item['path'].rsplit('/', 1)[0] if '/' in item['path'] else ''
        destination = parent + '/' + name if parent else name
        try:
            if item['kind'] == 'file':
                self.vault.move_file(item['id'], destination)
            else:
                actual = self.vault.move_folder(item['path'], destination)
                self.selected_id = 'dir:' + actual
            self._refresh_files()
        except Exception as exc:
            self._error(exc)

    def _move_selected(self):
        if self.busy or not self.selected_ids:
            return
        ids = list(self.selected_ids)
        available = ['(Vault root)'] + self.vault.list_folders()
        dialog = tk.Toplevel(self.root)
        dialog.title('Move inside VEIL')
        dialog.geometry('590x235')
        dialog.configure(bg=PANEL)
        dialog.transient(self.root)
        dialog.grab_set()
        tk.Label(dialog, text=f'Move {len(ids)} selected item(s)', bg=PANEL, fg=FG,
                 font=(FONT, 13, 'bold')).pack(anchor='w', padx=20, pady=(20, 10))
        tk.Label(dialog, text='Choose destination folder', bg=PANEL, fg=MUTED).pack(anchor='w', padx=20)
        selected = tk.StringVar(value='(Vault root)')
        picker = ttk.Combobox(dialog, state='readonly', textvariable=selected, values=available)
        picker.pack(fill='x', padx=20, pady=10)
        def perform():
            destination = '' if selected.get() == '(Vault root)' else selected.get()
            try:
                self.vault.move_many(ids, destination)
                dialog.destroy()
                self.selected_id = None
                self.selected_ids = set()
                self._refresh_files()
            except Exception as exc:
                messagebox.showerror('VEIL', str(exc), parent=dialog)
        ttk.Button(dialog, text='Move', style='Accent.TButton', command=perform).pack(anchor='e', padx=20, pady=5)

    def _drop_on_tree(self, event):
        destination = self.current_folder
        row = self.tree.identify_row(event.y)
        entry = self.visible_entries.get(row)
        if entry and entry['kind'] == 'folder':
            destination = entry['path']
        return self._handle_drop(event, destination)

    def _handle_drop(self, event, destination=None):
        if self.busy or not self.vault:
            return
        try:
            paths = self.root.tk.splitlist(event.data)
            self._import_paths(paths, destination if destination is not None else self.current_folder)
        except Exception as exc:
            self._error(f'Unable to read dropped files: {exc}')

    def _add_files(self):
        # ZIP archives and every other file type are already supported here.
        paths = filedialog.askopenfilenames(title='Select files (including ZIP archives) to encrypt',
                                            filetypes=[('All files', '*.*')])
        if paths:
            self._import_paths(paths, self.current_folder)

    def _add_zip(self):
        # Store the original archive as an encrypted file, without extracting it.
        # Both this button and drag-and-drop use the same encrypted import path.
        paths = filedialog.askopenfilenames(title='Select ZIP archives to encrypt',
                                            filetypes=[('ZIP archives', '*.zip')])
        if paths:
            self._import_paths(paths, self.current_folder)

    def _add_folder(self):
        folder = filedialog.askdirectory(title='Choose a folder to encrypt')
        if folder:
            self._import_paths([folder], self.current_folder)

    def _import_paths(self, paths, destination):
        if self.busy:
            return
        jobs = []
        folders = []
        try:
            for raw in paths:
                path = Path(raw).expanduser()
                if not path.exists() or path.is_symlink():
                    raise VaultError(f'Cannot import missing or linked item: {path}')
                resolved = path.resolve()
                if (resolved == self.vault.path or self.vault.path in resolved.parents
                        or (path.is_dir() and resolved in self.vault.path.parents)):
                    raise VaultError('Cannot import the vault folder or a directory containing the vault.')
                prefix = destination + '/' if destination else ''
                if path.is_file():
                    jobs.append((path, prefix + path.name))
                elif path.is_dir():
                    prefix += path.name
                    folders.append(prefix)
                    for child in path.rglob('*'):
                        if child.is_symlink():
                            continue
                        rel = child.relative_to(path).as_posix()
                        if child.is_dir():
                            folders.append(prefix + '/' + rel)
                        elif child.is_file():
                            jobs.append((child, prefix + '/' + rel))
            if not jobs and not folders:
                return
            if len(jobs) > 2000 and not messagebox.askyesno(
                   'Large import', f'Encrypt {len(jobs):,} files? This may take a while.', parent=self.root):
                return
            folders = sorted(set(folders), key=lambda x: (x.count('/'), x.casefold()))
            self._start_import(jobs, folders)
        except Exception as exc:
            self._error(exc)

    def _start_import(self, jobs, folders):
        self.busy = True
        self.file_status.configure(text=f'Importing: 0 / {len(jobs)} files ...')
        self._activity()
        vault = self.vault
        def worker():
            done = 0
            problems = []
            for folder in folders:
                try:
                    vault.ensure_folder(folder)
                except Exception as exc:
                    problems.append(f'{folder}: {exc}')
            for source, internal in jobs:
                try:
                    vault.import_file(source, internal)
                    done += 1
                except Exception as exc:
                    problems.append(f'{internal}: {exc}')
                if (done + len(problems)) % 8 == 0:
                    self.root.after(0, lambda n=done: self.file_status.configure(
                        text=f'Encrypting files: {n} / {len(jobs)} completed ...'))
            self.root.after(0, lambda: self._finish_import(done, problems))
        threading.Thread(target=worker, daemon=True).start()

    def _finish_import(self, done, problems):
        self.busy = False
        self._activity()
        if done:
            try:
                self.vault.log_activity('import', f'{done} files')
            except Exception:
                pass
        self._refresh_files()
        if problems:
            summary = '\n'.join(problems[:7])
            if len(problems) > 7:
                summary += f'\n… and {len(problems)-7} more errors.'
            messagebox.showwarning('VEIL import', f'Encrypted {done} file(s). '
                                   f'{len(problems)} item(s) could not be imported:\n\n{summary}',
                                   parent=self.root)
        elif done or self.vault.list_folders():
            self.file_status.configure(text=f'Encrypted {done} file(s) successfully. Originals remain unchanged.')

    def _export_file(self):
        if self.busy:
            return
        item = self._selection()
        if not item:
            return
        if item['kind'] == 'folder':
            self._export_folder(item)
            return
        target = filedialog.asksaveasfilename(title='Export decrypted file', initialfile=item['name'])
        if not target:
            return
        if not messagebox.askyesno('Export unencrypted file',
              'This creates a readable copy OUTSIDE the encrypted vault. Continue?', parent=self.root):
            return
        try:
            self.vault.export_file(item['id'], target)
            try: self.vault.log_activity('export','one file')
            except Exception: pass
            messagebox.showinfo('VEIL', 'Decrypted export completed and verified.', parent=self.root)
        except Exception as exc:
            self._error(exc)

    def _export_folder(self, item):
        target = filedialog.askdirectory(title='Choose an OUTSIDE folder for decrypted export')
        if not target:
            return
        root = Path(target).resolve() / item['name']
        if root.exists():
            self._error('Export destination folder already exists. Choose another location.')
            return
        if self.vault.path == root or self.vault.path in root.parents or root in self.vault.path.parents:
            self._error('Never export plaintext inside the encrypted vault or its parent folder.')
            return
        if not messagebox.askyesno('Export unencrypted folder',
             'All files in this folder will be decrypted and written to your selected destination. Continue?',
             parent=self.root):
            return
        try:
            folders = [name for name in self.vault.list_folders() if name == item['path'] or
                       name.startswith(item['path'] + '/')]
            root.mkdir()
            for name in folders:
                relative = name[len(item['path']):].lstrip('/')
                if relative:
                    (root / Path(relative)).mkdir(parents=True, exist_ok=True)
            for entry in self.vault.list_files():
                if entry['path'].startswith(item['path'] + '/'):
                    relative = entry['path'][len(item['path']) + 1:]
                    dest = root / Path(relative)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    self.vault.export_file(entry['file_id'], dest)
            try: self.vault.log_activity('export', 'folder')
            except Exception: pass
            messagebox.showinfo('VEIL', f'Folder exported to:\n{root}', parent=self.root)
        except Exception as exc:
            self._error(f'Folder export interrupted. Inspect and clean up any plaintext files at the destination.\n{exc}')

    def _preview_file(self):
        fid = self._selected_file()
        if not fid:
            return
        name = self.vault.manifest['items'][fid]['path']
        suffix = Path(name).suffix.casefold()
        if suffix == '.zip':
            self._open_zip(fid)
            return
        if suffix in VIDEO_EXTENSIONS:
            if not video_available():
                self._error('In-app video playback requires the media dependencies. Run Start-VEIL.bat.')
                return
            try:
                from .media import MAX_VIDEO_BYTES
                data = self.vault.read_small_file(fid, limit=MAX_VIDEO_BYTES)
                player = MemoryVideoPlayer(self.root, data, name,
                         on_close=lambda p: self.preview_windows.remove(p) if p in self.preview_windows else None)
                self.preview_windows.append(player)
            except Exception as exc:
                self._error(exc)
            return
        if suffix not in ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'):
            messagebox.showinfo('Preview', 'Photo and video previews and ZIP browsing are supported. '
                                'Export other formats to open them.', parent=self.root)
            return
        try:
            data = self.vault.read_small_file(fid)
            self._photo_window(data, name)
        except Exception as exc:
            self._error(exc)


    def _open_zip(self, file_id: str):
        """Verify/decrypt the encrypted ZIP into RAM, then open its read-only browser."""
        if not self.vault or self.busy:
            return
        file_info = self.vault.manifest['items'].get(file_id)
        if not file_info:
            return
        title = file_info['path'].rsplit('/', 1)[-1]
        vault = self.vault
        self.busy = True
        self.file_status.configure(text=f'Opening {title} securely… verifying encrypted data (no disk extraction).',
                                   fg=ACCENT)

        def worker():
            try:
                catalog = ZipCatalog(vault.read_verified_memory(file_id))
                err = None
            except Exception as exc:
                catalog, err = None, exc
            try:
                self.root.after(0, lambda: self._finish_open_zip(catalog, err, title, vault))
            except RuntimeError:
                if catalog:
                    catalog.close()

        threading.Thread(target=worker, daemon=True).start()

    def _finish_open_zip(self, catalog, error, title: str, vault):
        self.busy = False
        if not self.vault or self.vault is not vault:
            if catalog:
                catalog.close()
            return
        self.file_status.configure(text='ZIP contents are read-only; no files were extracted to disk.', fg=MUTED)
        if error:
            self._error(f'Could not open ZIP: {error}')
            return
        try:
            self._show_zip_browser(catalog, title)
        except Exception as exc:
            catalog.close()
            self._error(exc)

    def _show_zip_browser(self, catalog: ZipCatalog, title: str):
        win = tk.Toplevel(self.root)
        win.title(f'VEIL | {title} — ZIP contents')
        win.geometry('880x570')
        win.minsize(640, 420)
        win.configure(bg=BG)
        self.preview_windows.append(win)
        closed = False
        video_children = []

        def release(_event=None):
            nonlocal closed
            if closed:
                return
            closed = True
            for video_player in list(video_children):
                video_player.close()
            catalog.close()
            if win in self.preview_windows:
                self.preview_windows.remove(win)

        def close():
            release()
            if win.winfo_exists():
                win.destroy()

        win.protocol('WM_DELETE_WINDOW', close)
        win.bind('<Destroy>', lambda event: release() if event.widget is win else None, add='+')
        top = tk.Frame(win, bg=BG)
        top.pack(fill='x', padx=16, pady=(15, 10))
        tk.Label(top, text=f'Inside {title}', bg=BG, fg=FG,
                 font=(FONT, 16, 'bold')).pack(anchor='w')
        tk.Label(top, text='Browse folders and preview photos, video and text without exporting. '
                           'Only an explicit export writes an unencrypted file to disk.',
                 bg=BG, fg=MUTED, font=(FONT, 9), wraplength=810,
                 justify='left').pack(anchor='w', pady=(6, 0))
        actions = ttk.Frame(win)
        actions.pack(fill='x', padx=16, pady=(0, 9))
        frame = ttk.Frame(win)
        frame.pack(fill='both', expand=True, padx=16)
        tree = ttk.Treeview(frame, columns=('size', 'type'), show='tree headings', selectmode='browse')
        tree.heading('#0', text='Name')
        tree.column('#0', width=450, minwidth=250, stretch=True)
        tree.heading('size', text='Uncompressed size')
        tree.column('size', width=160, anchor='e', stretch=False)
        tree.heading('type', text='Type')
        tree.column('type', width=130, anchor='w', stretch=False)
        scroll = DarkScrollbar(frame, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        tree.pack(side='left', fill='both', expand=True)
        index_by_node = {}
        folder_nodes = {}
        items = catalog.entries

        def ensure_folder(path):
            if path in folder_nodes:
                return folder_nodes[path]
            parent = path.rsplit('/', 1)[0] if '/' in path else ''
            parent_node = ensure_folder(parent) if parent else ''
            node = tree.insert(parent_node, 'end', text='📁  ' + path.rsplit('/', 1)[-1],
                               values=('', 'Folder'), open=True)
            folder_nodes[path] = node
            return node

        for item in items:
            path = item['path']
            if item['kind'] == 'folder':
                ensure_folder(path)
            else:
                parent = path.rsplit('/', 1)[0] if '/' in path else ''
                parent_node = ensure_folder(parent) if parent else ''
                name = path.rsplit('/', 1)[-1]
                node = tree.insert(parent_node, 'end', text='📄  ' + name,
                                   values=(self._size_label(item['size']),
                                           Path(name).suffix.lower() or 'File'))
                index_by_node[node] = item['index']

        def selected_index():
            selection = tree.selection()
            if not selection:
                messagebox.showinfo('VEIL', 'Select a file inside the ZIP first.', parent=win)
                return None
            node = selection[0]
            if node not in index_by_node:
                tree.item(node, open=not tree.item(node, 'open'))
                return None
            return index_by_node[node]

        def preview(_event=None):
            if closed:
                return
            index = selected_index()
            if index is None:
                return
            info = catalog.infos[index]
            name = info.filename.rsplit('/', 1)[-1]
            ext = Path(name).suffix.casefold()
            photos = ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp')
            texts = ('.txt', '.md', '.csv', '.json', '.xml', '.log', '.ini', '.cfg',
                     '.yaml', '.yml', '.html', '.htm', '.py', '.js', '.ts', '.css',
                     '.bat', '.ps1', '.sh')
            if ext in VIDEO_EXTENSIONS:
                if not video_available():
                    messagebox.showwarning('ZIP video playback',
                        'The optional video decoder is missing. Close VEIL and run Start-VEIL.bat '
                        'to install PyAV, then reopen the archive.', parent=win)
                    return
                try:
                    raw = catalog.read_video_preview(index)
                    def on_video_close(player):
                        if player in video_children:
                            video_children.remove(player)
                        if player in self.preview_windows:
                            self.preview_windows.remove(player)
                    player = MemoryVideoPlayer(win, raw, f'{title} / {info.filename}',
                                               on_close=on_video_close)
                    video_children.append(player)
                    self.preview_windows.append(player)
                except Exception as exc:
                    messagebox.showerror('VEIL video', str(exc), parent=win)
                return
            if ext not in photos + texts:
                messagebox.showinfo('ZIP browser', 'This file type cannot be previewed yet. '
                    'You can see its name and size here, or export that file to open it.', parent=win)
                return
            try:
                raw = catalog.read_preview(index)
                if ext in photos:
                    self._photo_window(raw, f'{title} / {info.filename}')
                else:
                    try:
                        content = raw.decode('utf-8-sig')
                    except UnicodeDecodeError:
                        content = raw.decode('cp1252', errors='replace')
                    textwin = tk.Toplevel(win)
                    textwin.title(f'VEIL | {info.filename} (read-only)')
                    textwin.geometry('800x570')
                    textwin.configure(bg=BG)
                    self.preview_windows.append(textwin)
                    def release_text(event):
                        if event.widget is textwin and textwin in self.preview_windows:
                            self.preview_windows.remove(textwin)
                    textwin.bind('<Destroy>', release_text, add='+')
                    container = ttk.Frame(textwin)
                    container.pack(fill='both', expand=True, padx=12, pady=12)
                    text = tk.Text(container, bg=PANEL, fg=FG, insertbackground=FG,
                                   wrap='none', font=('Consolas', 10), state='normal')
                    scroll_y = DarkScrollbar(container, command=text.yview)
                    text.configure(yscrollcommand=scroll_y.set)
                    scroll_y.pack(side='right', fill='y')
                    text.pack(side='left', fill='both', expand=True)
                    text.insert('1.0', content)
                    text.configure(state='disabled')
            except Exception as exc:
                self._error(exc)

        def export_one():
            if closed:
                return
            index = selected_index()
            if index is None:
                return
            original = catalog.infos[index].filename.rsplit('/', 1)[-1]
            destination = filedialog.asksaveasfilename(parent=win,
                                title='Export decrypted file from ZIP', initialfile=original)
            if not destination:
                return
            try:
                if self.vault.path == Path(destination).resolve() or self.vault.path in Path(destination).resolve().parents:
                    raise VaultError('Do not export plaintext into the VEIL vault folder.')
                catalog.export_member(index, destination)
                messagebox.showinfo('VEIL', f'Extracted file to:\n{destination}', parent=win)
            except Exception as exc:
                self._error(exc)

        ttk.Button(actions, text='Preview selected', command=preview).pack(side='left', padx=(0, 8))
        ttk.Button(actions, text='Export selected file', command=export_one).pack(side='left', padx=(0, 8))
        ttk.Button(actions, text='Close ZIP', command=close).pack(side='right')
        tree.bind('<Double-1>', preview)
        tree.bind('<Return>', preview)
        count = sum(entry['kind'] == 'file' for entry in items)
        suffix = f'  •  {catalog.unsafe_count} unsafe ZIP paths hidden' if catalog.unsafe_count else ''
        tk.Label(win, text=f'{count:,} file(s)  •  Read-only ZIP preview in memory{suffix}',
                 bg=BG, fg=MUTED, anchor='w').pack(fill='x', padx=16, pady=9)

    def _delete_file(self):
        if self.busy or not self.selected_ids:
            return
        ids = list(self.selected_ids)
        if not messagebox.askyesno('Move to encrypted Recycle Bin',
                    f'Move {len(ids)} selected item(s) to the encrypted Recycle Bin?\n'
                    'You can restore them later.', parent=self.root):
            return
        try:
            self.vault.trash_many(ids)
            self.selected_ids = set()
            self.selected_id = None
            self._refresh_files()
        except Exception as exc:
            self._error(exc)

    def _gallery_tab(self, parent):
        tk.Label(parent, text='Encrypted Media Gallery', bg=BG, fg=FG,
                 font=(FONT, 21, 'bold')).pack(anchor='w', pady=(5, 6))
        tk.Label(parent, text='Photos and videos stay encrypted on disk. Thumbnails are decoded only while unlocked.',
                 bg=BG, fg=MUTED, wraplength=850).pack(anchor='w', pady=(0, 14))
        top = ttk.Frame(parent)
        top.pack(fill='x', pady=(0, 10))
        self.gallery_filter = tk.StringVar(value='All media')
        ttk.Combobox(top, state='readonly', width=20, textvariable=self.gallery_filter,
                     values=['All media', 'Photos only', 'Videos only']).pack(side='left')
        self.gallery_filter.trace_add('write', lambda *_: self._refresh_gallery())
        ttk.Button(top, text='Refresh Gallery', command=self._refresh_gallery).pack(side='left', padx=10)
        self.gallery_caption = tk.Label(top, bg=BG, fg=MUTED)
        self.gallery_caption.pack(side='right')
        area = tk.Frame(parent, bg=PANEL)
        area.pack(fill='both', expand=True)
        self.gallery_canvas = tk.Canvas(area, bg=PANEL, highlightthickness=0)
        scrollbar = DarkScrollbar(area, command=self.gallery_canvas.yview)
        self.gallery_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.gallery_canvas.pack(side='left', fill='both', expand=True)
        self.gallery_inner = tk.Frame(self.gallery_canvas, bg=PANEL)
        wid = self.gallery_canvas.create_window((0, 0), window=self.gallery_inner, anchor='nw')
        self.gallery_inner.bind('<Configure>', lambda _e: self.gallery_canvas.configure(scrollregion=self.gallery_canvas.bbox('all')))
        self.gallery_canvas.bind('<Configure>', lambda e: self.gallery_canvas.itemconfigure(wid, width=e.width))
        self._gallery_refs = []
        self._refresh_gallery()

    def _refresh_gallery(self):
        if not getattr(self, 'gallery_inner', None) or not self.gallery_inner.winfo_exists() or not self.vault:
            return
        for child in self.gallery_inner.winfo_children():
            child.destroy()
        self._gallery_refs = []
        photos = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}
        videos = VIDEO_EXTENSIONS
        mode = self.gallery_filter.get()
        all_media = [x for x in self.vault.list_files() if Path(x['path']).suffix.casefold() in (photos | videos)]
        files = [x for x in all_media if mode == 'All media' or
                 (mode == 'Photos only' and Path(x['path']).suffix.casefold() in photos) or
                 (mode == 'Videos only' and Path(x['path']).suffix.casefold() in videos)]
        shown = files[:240]  # bound thumbnail decoding and widget count
        self.gallery_caption.configure(text=f'{len(files)} media item(s)' + (' • displaying first 240' if len(files)>240 else ''))
        for idx, obj in enumerate(shown):
            fid = obj['file_id']
            pane = tk.Frame(self.gallery_inner, bg=PANEL2, width=188, height=170,
                            highlightthickness=1, highlightbackground=BORDER)
            pane.grid(row=idx//4, column=idx%4, padx=9, pady=9, sticky='nw')
            pane.grid_propagate(False)
            item = {'id': fid, 'kind': 'file', 'name': obj['path'].rsplit('/',1)[-1], 'size':obj['size']}
            image = (self._thumbnail(item, 112) if Path(obj['path']).suffix.casefold() in photos
                     else self._video_thumbnail(item))
            if image:
                self._gallery_refs.append(image)
                graphic = tk.Label(pane, image=image, bg=PANEL2, cursor='hand2')
            else:
                graphic = tk.Label(pane, text=('▶' if Path(obj['path']).suffix.casefold() in videos else '▧'),
                                   bg=PANEL2, fg=ACCENT, font=(FONT, 32), cursor='hand2')
            graphic.pack(fill='both', expand=True, pady=(5,0))
            caption = tk.Label(pane, text=item['name'], bg=PANEL2, fg=FG, font=(FONT, 9),
                               wraplength=175, cursor='hand2')
            caption.pack(pady=5)
            for w in (graphic,caption,pane):
                w.bind('<Button-1>', lambda e, identity=fid: self._gallery_open(identity))

    def _gallery_open(self, file_id):
        if file_id not in self.vault.manifest['items']:
            return
        path = self.vault.manifest['items'][file_id]['path']
        self.current_folder = path.rsplit('/', 1)[0] if '/' in path else ''
        self.selected_ids = {file_id}
        self.selected_id = file_id
        self._select_nav(0)
        self._refresh_files()
        self._preview_file()

    def _trash_tab(self, parent):
        tk.Label(parent, text='Encrypted Recycle Bin', bg=BG, fg=FG,
                 font=(FONT, 21, 'bold')).pack(anchor='w', pady=(5, 7))
        tk.Label(parent, text='Deleted items remain encrypted until you permanently remove them. Restore folders before their files.',
                 bg=BG, fg=MUTED, wraplength=860).pack(anchor='w', pady=(0, 10))
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=(0, 12))
        ttk.Button(row, text='Restore selected', command=self._restore_trash).pack(side='left', padx=(0, 9))
        ttk.Button(row, text='Restore all', command=lambda: self._restore_trash(all_items=True)).pack(side='left', padx=(0, 9))
        ttk.Button(row, text='Permanently delete selected', style='Danger.TButton', command=self._purge_trash).pack(side='left')
        ttk.Button(row, text='Empty Bin', style='Danger.TButton', command=lambda: self._purge_trash(all_items=True)).pack(side='left', padx=9)
        self.recycle_tree = ttk.Treeview(parent, columns=('name','size','when'), show='headings',selectmode='extended')
        for key, title, width in [('name', 'Original location', 440), ('size','Size', 100), ('when','Deleted (UTC)', 220)]:
            self.recycle_tree.heading(key,text=title)
            self.recycle_tree.column(key,width=width)
        self.recycle_tree.pack(fill='both', expand=True)
        self._refresh_trash()

    def _refresh_trash(self):
        if not getattr(self,'recycle_tree',None) or not self.recycle_tree.winfo_exists() or not self.vault:
            return
        self.recycle_tree.delete(*self.recycle_tree.get_children())
        for obj in self.vault.trash_entries():
            self.recycle_tree.insert('', 'end', iid=obj['id'], values=(obj['path'],
                     self._size_label(obj.get('size')), obj.get('deleted_at','')))

    def _restore_trash(self, all_items=False):
        if self.busy: return
        ids = (list(self.recycle_tree.get_children()) if all_items else list(self.recycle_tree.selection()))
        if not ids:
            return
        # Sort parent folders first so a fully deleted hierarchy can be restored.
        ids.sort(key=lambda name: (0 if name.startswith('dir:') else 1, name.count('/')))
        try:
            count = self.vault.restore_trash(ids)
            self._refresh_trash()
            self._refresh_files()
            messagebox.showinfo('VEIL', f'Restored {count} item(s).', parent=self.root)
        except Exception as exc:
            self._error(exc)

    def _purge_trash(self, all_items=False):
        if self.busy: return
        ids = (list(self.recycle_tree.get_children()) if all_items else list(self.recycle_tree.selection()))
        if not ids:
            return
        if not messagebox.askyesno('Permanent deletion',
             f'Permanently delete {len(ids)} selected item(s)?\nThis cannot be undone and is not a secure SSD wipe.',
             parent=self.root):
            return
        try:
            self.vault.purge_trash(ids)
            self._refresh_trash()
        except Exception as exc:
            self._error(exc)

    def _activity_tab(self, parent):
        tk.Label(parent, text='Security Activity', bg=BG, fg=FG,
                 font=(FONT, 21, 'bold')).pack(anchor='w', pady=(5, 7))
        tk.Label(parent, text='Encrypted event history, including successful and failed password attempts. Timestamps use UTC.',
                 bg=BG, fg=MUTED).pack(anchor='w', pady=(0, 13))
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=(0, 9))
        self.activity_filter = tk.StringVar(value='All events')
        ttk.Combobox(row, textvariable=self.activity_filter, state='readonly', width=21,
                     values=('All events', 'Unlock attempts', 'File operations', 'Backups')).pack(side='left')
        self.activity_filter.trace_add('write', lambda *_: self._refresh_activity())
        ttk.Button(row, text='Refresh',command=self._refresh_activity).pack(side='left', padx=9)
        self.activity_tree = ttk.Treeview(parent, columns=('time','event','details'), show='headings', selectmode='browse')
        for key,title,width in [('time','Time (UTC)',180),('event','Event',200),('details','Details',350)]:
            self.activity_tree.heading(key,text=title)
            self.activity_tree.column(key,width=width)
        self.activity_tree.pack(fill='both',expand=True)
        self._refresh_activity()

    def _refresh_activity(self):
        if not getattr(self,'activity_tree',None) or not self.activity_tree.winfo_exists() or not self.vault:
            return
        self.activity_tree.delete(*self.activity_tree.get_children())
        selected = self.activity_filter.get()
        for i, ev in enumerate(self.vault.activity()):
            action = ev.get('event','')
            if selected == 'Unlock attempts' and 'unlock' not in action: continue
            if selected == 'Backups' and 'backup' not in action: continue
            if selected == 'File operations' and action not in ('import','export','bulk_move','bulk_trashed','file_trashed','folder_trashed','trash_restored','trash_permanently_deleted'):continue
            self.activity_tree.insert('', 'end', iid=str(i), values=(ev.get('at',''), action, ev.get('detail','')))

    def _security_tab(self, parent):
        tk.Label(parent, text='Security Photos', bg=BG, fg=FG,
                 font=(FONT, 21, 'bold')).pack(anchor='w', pady=(5, 7))
        tk.Label(parent, text='When enabled, VEIL takes one encrypted webcam photo on vault opening. '
                              'Select an entry to preview it; double-click or press Enter to open a larger view. '
                              'Photos stay on this drive and are never uploaded.',
                 bg=BG, fg=MUTED, wraplength=850, justify='left').pack(anchor='w', pady=(0, 9))

        # Keep controls above the growing list. Previously these buttons were
        # packed *after* a Treeview whose requested height exceeded the pane.
        # On typical 720p screens, that placed the controls below the window.
        controls = ttk.Frame(parent)
        controls.pack(fill='x', pady=(0, 10))
        self.view_capture_button = ttk.Button(controls, text='View selected photo',
                                              command=self._view_capture)
        self.view_capture_button.pack(side='left', padx=(0, 8))
        self.delete_capture_button = ttk.Button(controls, text='Delete selected photo',
                                                command=self._delete_capture)
        self.delete_capture_button.pack(side='left', padx=(0, 8))
        ttk.Button(controls, text='Refresh', command=self._refresh_captures).pack(side='left')
        self.camera_status = tk.Label(controls, text='', bg=BG, fg=MUTED,
                                      font=(FONT, 9))
        self.camera_status.pack(side='right', padx=(8, 0))

        gallery = ttk.PanedWindow(parent, orient='horizontal')
        gallery.pack(fill='both', expand=True)
        list_panel = ttk.Frame(gallery)
        preview_panel = ttk.Frame(gallery, style='Card.TFrame')
        gallery.add(list_panel, weight=2)
        gallery.add(preview_panel, weight=3)

        self.camera_list = ttk.Treeview(list_panel, columns=('time',), show='headings',
                                        height=6, selectmode='browse')
        self.camera_list.heading('time', text='Camera capture time (UTC)')
        self.camera_list.column('time', width=285, minwidth=160, stretch=True)
        scrollbar = DarkScrollbar(list_panel, command=self.camera_list.yview)
        self.camera_list.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.camera_list.pack(side='left', fill='both', expand=True)
        self.camera_list.bind('<<TreeviewSelect>>', self._select_capture)
        self.camera_list.bind('<Double-1>', self._view_capture)
        self.camera_list.bind('<Return>', self._view_capture)

        tk.Label(preview_panel, text='PHOTO PREVIEW', bg=PANEL, fg=MUTED,
                 font=(FONT, 9, 'bold')).pack(anchor='w', padx=16, pady=(12, 0))
        self.camera_preview_label = tk.Label(preview_panel,
                                             text='Select a login snapshot to view it.',
                                             bg=PANEL, fg=MUTED,
                                             font=(FONT, 10), justify='center')
        self.camera_preview_label.pack(fill='both', expand=True, padx=12, pady=12)
        self._refresh_captures()

    def _clear_capture_preview(self, message: str = 'Select a login snapshot to view it.'):
        self.capture_preview_photo = None
        if hasattr(self, 'camera_preview_label') and self.camera_preview_label.winfo_exists():
            self.camera_preview_label.configure(image='', text=message)

    def _refresh_captures(self):
        previous = self.camera_list.selection()
        previous_id = previous[0] if previous else None
        for item_id in self.camera_list.get_children():
            self.camera_list.delete(item_id)
        entries = self.vault.camera_entries()
        for item in entries:
            self.camera_list.insert('', 'end', iid=item['id'],
                                    values=(item['captured_at'],))
        self.camera_status.configure(text=f'{len(entries)} encrypted photo(s)')
        if entries:
            chosen = previous_id if previous_id in self.camera_list.get_children() else entries[0]['id']
            self.camera_list.selection_set(chosen)
            self.camera_list.focus(chosen)
            self.camera_list.see(chosen)
            self._select_capture()
        else:
            self._clear_capture_preview('No login photos recorded yet.\n\n'
                                        'Enable the login camera in Settings, then reopen VEIL.')

    def _select_capture(self, _event=None):
        selected = self.camera_list.selection()
        if not selected:
            self._clear_capture_preview()
            return
        try:
            from PIL import Image, ImageTk
            data = self.vault.open_camera_image(selected[0])
            image = Image.open(io.BytesIO(data))
            image.thumbnail((420, 340))
            photo = ImageTk.PhotoImage(image, master=self.root)
            self.capture_preview_photo = photo  # Hold reference while unlocked.
            self.camera_preview_label.configure(image=photo, text='')
        except Exception as exc:
            self._clear_capture_preview('This snapshot could not be displayed.')
            self._error(exc)

    def _view_capture(self, _event=None):
        selected = self.camera_list.selection()
        if not selected:
            messagebox.showinfo('Security photos', 'Select a photo first.', parent=self.root)
            return
        try:
            self._photo_window(self.vault.open_camera_image(selected[0]),
                               'VEIL | Encrypted login snapshot')
        except Exception as exc:
            self._error(exc)

    def _delete_capture(self):
        selected = self.camera_list.selection()
        if selected and messagebox.askyesno('Delete snapshot',
                    'Permanently delete this encrypted login photo?', parent=self.root):
            try:
                self.vault.delete_camera_image(selected[0])
                self._clear_capture_preview()
                self._refresh_captures()
            except Exception as exc:
                self._error(exc)

    def _close_previews(self):
        for window in list(self.preview_windows):
            try:
                if isinstance(window, MemoryVideoPlayer):
                    window.close()
                elif window.winfo_exists():
                    window.destroy()
            except (tk.TclError, RuntimeError):
                pass
        self.preview_windows.clear()

    def _photo_window(self, data: bytes, title: str):
        from PIL import Image, ImageTk
        image = Image.open(io.BytesIO(data))
        image.thumbnail((1000, 700))
        window = tk.Toplevel(self.root)
        self.preview_windows.append(window)
        window.title(title)
        window.configure(bg=BG)
        photo = ImageTk.PhotoImage(image)
        label = tk.Label(window, image=photo, bg=BG)
        label.image = photo
        label.pack(padx=15, pady=15)

    def _settings_tab(self, parent):
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
        scroll = DarkScrollbar(parent, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        area = tk.Frame(canvas, bg=BG)
        wid = canvas.create_window((0, 0), window=area, anchor='nw')
        area.bind('<Configure>', lambda _event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(wid, width=event.width))
        canvas.bind('<MouseWheel>',lambda event:canvas.yview_scroll(-int(event.delta/120), 'units'))
        tk.Label(area, text='Settings', bg=BG, fg=FG,
                 font=(FONT, 21, 'bold')).pack(anchor='w', pady=(6, 16))
        opts = self.vault.preferences()
        pane = ttk.Frame(area, style='Card.TFrame', padding=22)
        pane.pack(fill='x', pady=(0,12))
        tk.Label(pane, text='Automatic vault locking', font=(FONT, 15, 'bold'),bg=PANEL, fg=FG).pack(anchor='w')
        self.idle_var = tk.StringVar(value=str(opts.get('idle_minutes',10)))
        timeout_line = ttk.Frame(pane, style='Card.TFrame')
        timeout_line.pack(fill='x', pady=9)
        tk.Label(timeout_line, text='Lock after inactivity (minutes)', bg=PANEL, fg=FG).pack(side='left')
        ttk.Combobox(timeout_line, textvariable=self.idle_var, state='readonly', width=9,
                     values=('0','1','5','10','15','30','60')).pack(side='left', padx=13)
        self.windows_lock_var = tk.BooleanVar(value=opts.get('lock_windows',True))
        self.sleep_lock_var = tk.BooleanVar(value=opts.get('lock_sleep',True))
        ttk.Checkbutton(pane, text='Lock when the Windows session locks (best-effort Windows detection)',
                        variable=self.windows_lock_var).pack(anchor='w',pady=4)
        ttk.Checkbutton(pane, text='Lock after Windows resumes from sleep or long suspension',
                        variable=self.sleep_lock_var).pack(anchor='w',pady=4)
        ttk.Button(pane, text='Save auto-lock settings',command=self._save_lock_settings).pack(anchor='w',pady=9)

        cam = ttk.Frame(area, style='Card.TFrame', padding=22)
        cam.pack(fill='x', pady=(0,12))
        tk.Label(cam, text='Login webcam security',font=(FONT, 15, 'bold'),bg=PANEL,fg=FG).pack(anchor='w')
        self.camera_var = tk.BooleanVar(value=self.vault.config.get('capture_enabled',False))
        ttk.Checkbutton(cam, text='Take an encrypted webcam photo on each new opening of this vault',
                        variable=self.camera_var).pack(anchor='w',pady=8)
        self._note(cam, 'Opt-in. An enabled camera attempts one capture before password entry. Photos remain encrypted and offline.')
        ttk.Button(cam, text='Save camera preference',command=self._save_camera_setting).pack(anchor='w',pady=8)

        backup = ttk.Frame(area, style='Card.TFrame',padding=22)
        backup.pack(fill='x',pady=(0,12))
        tk.Label(backup,text='Encrypted vault backups',font=(FONT,15,'bold'),bg=PANEL,fg=FG).pack(anchor='w')
        self.backup_dest_var = tk.StringVar(value=opts.get('backup_folder',''))
        ttk.Entry(backup, textvariable=self.backup_dest_var).pack(fill='x',pady=(12,4))
        ttk.Button(backup,text='Choose backup folder',command=self._choose_backup_folder).pack(anchor='w',pady=(2,10))
        tk.Label(backup,text='Automatic backup cadence (while VEIL is open and unlocked)',bg=PANEL,fg=FG).pack(anchor='w')
        self.backup_cadence = tk.StringVar(value={0:'Off',24:'Daily',168:'Weekly'}.get(opts.get('backup_hours',0),'Off'))
        ttk.Combobox(backup,textvariable=self.backup_cadence,state='readonly',width=12,
                     values=('Off','Daily','Weekly')).pack(anchor='w',pady=7)
        buttons = ttk.Frame(backup, style='Card.TFrame')
        buttons.pack(fill='x',pady=5)
        ttk.Button(buttons,text='Save backup settings',command=self._save_backup_settings).pack(side='left',padx=(0,9))
        ttk.Button(buttons,text='Back up now',style='Accent.TButton',command=self._backup_now).pack(side='left')
        ttk.Button(buttons,text='Restore encrypted backup copy…',command=self._restore_backup).pack(side='left',padx=9)
        self.backup_status = tk.Label(backup,bg=PANEL,fg=MUTED,text='Backups contain encrypted data. Keep the recovery key separately.')
        self.backup_status.pack(anchor='w',pady=7)

        health = ttk.Frame(area,style='Card.TFrame',padding=22)
        health.pack(fill='x',pady=(0,12))
        tk.Label(health,text='Vault health check',font=(FONT,15,'bold'),bg=PANEL,fg=FG).pack(anchor='w')
        self.health_status = tk.Label(health,bg=PANEL,fg=MUTED,text='Verify encrypted object authentication and detect missing/corrupted files.')
        self.health_status.pack(anchor='w',pady=9)
        ttk.Button(health,text='Run integrity check',command=self._health_check).pack(anchor='w')

        security = ttk.Frame(area,style='Card.TFrame',padding=22)
        security.pack(fill='x',pady=(0,12))
        tk.Label(security,text='Vault access and profiles',font=(FONT,15,'bold'),bg=PANEL,fg=FG).pack(anchor='w')
        ttk.Button(security,text='Change master password',command=self._change_password).pack(anchor='w',pady=5)
        ttk.Button(security,text='Switch vault / create another vault',command=self._switch_vault).pack(anchor='w',pady=5)
        ttk.Button(security,text='Lock now',command=self.lock).pack(anchor='w',pady=5)
        self._note(security, 'Windows Hello passwordless unlock is not enabled in this prototype. It needs a securely hardware-bound key implementation; a biometric prompt alone would not encrypt or protect your vault key.')

        updates = ttk.Frame(area, style='Card.TFrame', padding=22)
        updates.pack(fill='x', pady=(0,12))
        tk.Label(updates, text='GitHub updates', font=(FONT,15,'bold'), bg=PANEL, fg=FG).pack(anchor='w')
        tk.Label(updates, text=f'Current version: {__version__}  •  Unsigned experimental preview',
                 bg=PANEL, fg=MUTED).pack(anchor='w', pady=7)
        self.update_check_var = tk.BooleanVar(value=self.auto_check_updates)
        ttk.Checkbutton(updates, text='Check GitHub for newer versions on startup (opt-in)',
                        variable=self.update_check_var, command=self._set_update_check).pack(anchor='w', pady=4)
        self._note(updates, 'Checks only publicly published GitHub releases and sends no vault contents. '
                   'Updates are never downloaded or installed without your involvement.')
        ttk.Button(updates, text='Check for updates',
                   command=lambda: self._check_updates(show_current=True)).pack(anchor='w', pady=8)

    def _set_update_check(self):
        self.auto_check_updates = bool(self.update_check_var.get())
        self._save_recent()

    def _check_updates(self, *, show_current: bool):
        """Only reads public release metadata; never installs an update."""
        def worker():
            try:
                update = find_update()
                error = None
            except Exception as exc:
                update = None
                error = str(exc)
            try:
                self.root.after(0, lambda: self._show_update_result(update, error, show_current))
            except (RuntimeError, tk.TclError):
                pass
        threading.Thread(target=worker,daemon=True).start()

    def _show_update_result(self, update, error, show_current):
        if error:
            if show_current:
                messagebox.showwarning('VEIL updates', 'Update check failed: '+error,parent=self.root)
        elif update:
            if messagebox.askyesno('VEIL update available',
                   f"A newer release ({update['tag']}) is available on GitHub.\n\n"
                   'Open the official Releases page to review and download it?\n'
                   'VEIL will not install software automatically.', parent=self.root):
                webbrowser.open(GITHUB_RELEASES_URL)
        elif show_current:
            messagebox.showinfo('VEIL updates','No newer published releases were found.',parent=self.root)

    def _save_lock_settings(self):
        if self.busy: return
        try:
            self.vault.set_preferences(idle_minutes=int(self.idle_var.get()),
                  lock_windows=self.windows_lock_var.get(),lock_sleep=self.sleep_lock_var.get())
            messagebox.showinfo('VEIL','Automatic locking settings saved.',parent=self.root)
        except Exception as exc: self._error(exc)

    def _choose_backup_folder(self):
        directory = filedialog.askdirectory(title='Select a separate folder for encrypted vault backups')
        if directory:
            self.backup_dest_var.set(directory)

    def _save_backup_settings(self):
        if self.busy: return
        try:
            dest = self.backup_dest_var.get().strip()
            hours = {'Off':0,'Daily':24,'Weekly':168}[self.backup_cadence.get()]
            if hours and (not dest or not Path(dest).is_dir()):
                raise VaultError('Choose an existing backup destination first.')
            self.vault.set_preferences(backup_folder=dest)
            self.vault.set_backup_schedule(hours)
            messagebox.showinfo('VEIL','Encrypted backup settings saved.',parent=self.root)
        except Exception as exc: self._error(exc)

    def _maybe_auto_backup(self):
        if self.busy or self._backup_in_progress or not self.vault:
            return
        opts = self.vault.preferences()
        hours = int(opts.get('backup_hours',0))
        destination = opts.get('backup_folder','')
        if not hours or not destination or not Path(destination).is_dir():
            return
        last = opts.get('last_backup_utc','')
        try:
            last_dt = dt.datetime.fromisoformat(last)
        except (ValueError, TypeError):
            last_dt = dt.datetime(2000,1,1,tzinfo=dt.timezone.utc)
        if dt.datetime.now(dt.timezone.utc) - last_dt >= dt.timedelta(hours=hours):
            self._run_backup(destination,automatic=True)

    def _backup_now(self):
        if self.busy or self._backup_in_progress:
            return
        directory = self.backup_dest_var.get().strip()
        if not directory:
            messagebox.showwarning('VEIL','Choose a backup folder first.',parent=self.root)
            return
        if not messagebox.askyesno('Encrypted vault backup',
              'Copy the entire encrypted vault to this backup folder?\n\nKeep the recovery key separately.',
              parent=self.root): return
        self._run_backup(directory,automatic=False)

    def _run_backup(self,folder,automatic):
        self.busy = True
        self._backup_in_progress = True
        vault = self.vault
        if getattr(self,'backup_status',None) and self.backup_status.winfo_exists():
            self.backup_status.configure(text='Copying and verifying encrypted backup…')
        def worker():
            try:
                result = vault.backup_to(folder)
                error = None
            except Exception as exc:
                result, error = None, str(exc)
            self.root.after(0,lambda:self._backup_done(result,error,automatic))
        threading.Thread(target=worker,daemon=True).start()

    def _backup_done(self,path,error,automatic):
        self.busy = False
        self._backup_in_progress = False
        self.auto_check_updates = False
        if self.vault and path:
            hours = int(self.vault.preferences().get('backup_hours',0))
            self.vault.set_backup_schedule(hours,last_at=dt.datetime.now(dt.timezone.utc).isoformat())
        if getattr(self,'backup_status',None) and self.backup_status.winfo_exists():
            self.backup_status.configure(text=('Backup failed: ' + error if error else 'Verified backup: ' + str(path)))
        if error:
            messagebox.showwarning('VEIL backup',error,parent=self.root)
        elif not automatic:
            messagebox.showinfo('VEIL','Encrypted backup verified:\n'+str(path),parent=self.root)

    def _restore_backup(self):
        if self.busy:
            return
        source = filedialog.askdirectory(title='Select a complete VEIL-Backup folder')
        if not source:
            return
        target = filedialog.askdirectory(title='Where should the restored encrypted vault be created?')
        if not target:
            return
        if not messagebox.askyesno('Restore backup',
            'Copy the encrypted backup to a NEW vault folder without overwriting anything?'
            '\nYou will need its original password or recovery key.',parent=self.root):
            return
        self.busy = True
        def worker():
            try:
                dest = Vault.restore_backup_copy(source, target)
                err = None
            except Exception as exc:
                dest, err = None, str(exc)
            self.root.after(0, lambda: self._restore_backup_done(dest,err))
        threading.Thread(target=worker,daemon=True).start()

    def _restore_backup_done(self,path,error):
        self.busy = False
        if error:
            self._error(error)
        else:
            messagebox.showinfo('VEIL', 'Verified encrypted restore created at:\n'+str(path)+
                '\nUse Open existing vault at the login screen to access it.',parent=self.root)

    def _health_check(self):
        if self.busy or not self.vault:
            return
        self.busy = True
        vault = self.vault
        if self.health_status.winfo_exists():
            self.health_status.configure(text='Checking encrypted file authentication…')
        def worker():
            try:
                answer = vault.health_check()
                error = None
            except Exception as exc:
                answer, error = None, str(exc)
            self.root.after(0,lambda:self._health_done(answer,error))
        threading.Thread(target=worker,daemon=True).start()

    def _health_done(self,answer,error):
        self.busy = False
        if error:
            self.health_status.configure(text=error)
            return
        summary = (f"Checked {answer['checked']} files + {answer['captures']} snapshots; "
                   f"{len(answer['errors'])} bad files, {len(answer['capture_errors'])} bad snapshots; "
                   f"{answer['orphans']} orphaned blobs")
        self.health_status.configure(text=summary)
        try: self.vault.log_activity('health_check',summary)
        except Exception: pass
        if answer['errors'] or answer['capture_errors']:
            messagebox.showwarning('Vault health check',summary+'\n\n'+
                  str((answer['errors'] + answer['capture_errors'])[:12]),parent=self.root)
        else:
            messagebox.showinfo('Vault health check',summary+'\nNo file integrity errors found.',parent=self.root)

    def _switch_vault(self):
        if self.busy: return
        self.lock()
    def _save_camera_setting(self):
        if self.busy: return
        enabled = self.camera_var.get()
        if enabled and not self.vault.config.get('capture_enabled'):
            if not messagebox.askyesno('Enable login camera?',
               'Allow VEIL to access your webcam once when this vault opens, '
               'capture a photo before password entry, and encrypt it locally? '\
               '\n\nYou can disable it here later; it does not identify people by face.'):
                self.camera_var.set(False)
                return
        try:
            self.vault.set_camera_enabled(enabled)
            try: self.vault.log_activity('camera_changed',str(enabled))
            except Exception: pass
            messagebox.showinfo('VEIL', 'Camera ' + ('enabled. It will capture on your next app launch.'
                                                   if enabled else 'disabled.'))
        except Exception as exc:
            self._error(exc)

    def _change_password(self):
        if self.busy: return
        new = simpledialog.askstring('New master password', 'Enter a new password (12+ characters):',
                                     show='●', parent=self.root)
        if not new:
            return
        repeat = simpledialog.askstring('Confirm master password', 'Enter it again:',
                                        show='●', parent=self.root)
        if new != repeat:
            self._error('Passwords did not match.')
            return
        try:
            self.vault.change_password(new)
            try: self.vault.log_activity('password_changed')
            except Exception: pass
            messagebox.showinfo('VEIL', 'Password changed. Your recovery key still works.')
        except Exception as exc:
            self._error(exc)

    def lock(self):
        if self.busy:
            messagebox.showwarning('VEIL', 'Finish the file import before locking.', parent=self.root)
            return
        self._close_previews()
        self._clear_capture_preview()
        self._thumbnail_cache = {}
        self.file_preview_photo = None
        self.search_var.set('')
        if self.vault:
            self.vault.lock()
        self.vault = None
        self.show_login()


def main():
    VeilApp().run()
