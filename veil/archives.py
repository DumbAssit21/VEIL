"""Read-only, in-memory ZIP browsing for already-authenticated VEIL data.

Only exports specifically selected members to disk on explicit user request.
Untrusted ZIP member names are filtered; no blanket ZIP extraction is provided.
"""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

from .core import VaultError

MAX_ARCHIVE_ENTRIES = 20000
MAX_PREVIEW_BYTES = 12 * 1024 * 1024
MAX_VIDEO_PREVIEW_BYTES = 160 * 1024 * 1024
MAX_EXPORT_BYTES = 2 * 1024 * 1024 * 1024


def safe_archive_path(name: str) -> str | None:
    """Return a safe display path, ignoring potentially hostile ZIP paths."""
    clean = name.rstrip('/')
    if not clean or len(clean) > 2048 or clean.count('/') > 64 or '\\' in clean or '\x00' in clean or clean.startswith('/'):
        return None
    parts = clean.split('/')
    if any(part in ('', '.', '..') or ':' in part or any(ord(c) < 32 for c in part)
           for part in parts):
        return None
    return '/'.join(parts)


class ZipCatalog:
    def __init__(self, plaintext_memory):
        self.memory = plaintext_memory
        self.archive = None
        try:
            self.archive = zipfile.ZipFile(self.memory, 'r')
            self.infos = self.archive.infolist()
            if len(self.infos) > MAX_ARCHIVE_ENTRIES:
                raise VaultError(f'ZIP contains too many entries (limit: {MAX_ARCHIVE_ENTRIES:,}).')
            self.unsafe_count = 0
            self.entries = self._catalog_entries()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.archive is not None:
            self.archive.close()
            self.archive = None
        if self.memory is not None:
            self.memory.close()
            self.memory = None

    def _catalog_entries(self) -> list[dict]:
        catalog = {}
        for index, info in enumerate(self.infos):
            path = safe_archive_path(info.filename)
            if path is None:
                self.unsafe_count += 1
                continue
            bits = path.split('/')
            for i in range(1, len(bits)):
                folder = '/'.join(bits[:i])
                catalog.setdefault(('folder', folder),
                                   {'kind': 'folder', 'path': folder, 'size': None, 'index': None})
            if info.is_dir():
                catalog.setdefault(('folder', path),
                                   {'kind': 'folder', 'path': path, 'size': None, 'index': None})
            else:
                # Keep distinct files that happen to have identical names in the ZIP.
                catalog[('file', index)] = {'kind': 'file', 'path': path,
                                             'size': info.file_size, 'index': index}
        return sorted(catalog.values(), key=lambda e: (e['path'].casefold(), e['kind'] != 'folder'))

    def _checked_member(self, index: int, max_size: int):
        if not isinstance(index, int) or index < 0 or index >= len(self.infos):
            raise VaultError('ZIP member was not found.')
        info = self.infos[index]
        if safe_archive_path(info.filename) is None or info.is_dir():
            raise VaultError('This is not a valid file inside the archive.')
        if stat.S_ISLNK(info.external_attr >> 16):
            raise VaultError('Symbolic links in ZIPs are not opened or extracted.')
        if info.file_size > max_size:
            raise VaultError(f'This ZIP member is too large (limit: {max_size // (1024*1024)} MiB).')
        if info.flag_bits & 0x1:
            raise VaultError('This ZIP contains password-protected files. '
                             'VEIL cannot open the inner ZIP password in this version.')
        return info

    def read_preview(self, index: int) -> bytes:
        info = self._checked_member(index, MAX_PREVIEW_BYTES)
        try:
            with self.archive.open(info, 'r') as member:
                data = member.read(MAX_PREVIEW_BYTES + 1)
            if len(data) > MAX_PREVIEW_BYTES:
                raise VaultError('This file exceeds the in-app preview size limit.')
            return data
        except (RuntimeError, zipfile.BadZipFile, OSError, EOFError) as exc:
            raise VaultError('Cannot read this ZIP entry; the archive may be damaged.') from exc

    def read_video_preview(self, index: int) -> bytes:
        """Read a capped video member into memory; never extract it to disk."""
        info = self._checked_member(index, MAX_VIDEO_PREVIEW_BYTES)
        try:
            with self.archive.open(info, 'r') as member:
                data = member.read(MAX_VIDEO_PREVIEW_BYTES + 1)
            if len(data) > MAX_VIDEO_PREVIEW_BYTES:
                raise VaultError('Video exceeds the 160 MiB in-app playback limit.')
            return data
        except (RuntimeError, zipfile.BadZipFile, OSError, EOFError) as exc:
            raise VaultError('Cannot decode video from ZIP; the archive may be damaged.') from exc

    def export_member(self, index: int, destination: str | Path) -> Path:
        info = self._checked_member(index, MAX_EXPORT_BYTES)
        destination = Path(destination)
        if destination.exists():
            raise VaultError('Destination already exists; VEIL will not overwrite it.')
        if not destination.parent.exists():
            raise VaultError('Export destination folder does not exist.')
        fd, temporary = tempfile.mkstemp(prefix='.veil-zip-export-', dir=destination.parent)
        try:
            with os.fdopen(fd, 'wb') as output, self.archive.open(info, 'r') as source:
                amount = 0
                while chunk := source.read(1024 * 1024):
                    amount += len(chunk)
                    if amount > MAX_EXPORT_BYTES:
                        raise VaultError('Uncompressed ZIP entry exceeds the export size limit.')
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            # Atomic no-overwrite on NTFS, or exclusive-create fallback on other drives.
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise VaultError('Destination already exists.') from exc
            except OSError:
                try:
                    fd2 = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError as exc:
                    raise VaultError('Destination already exists.') from exc
                try:
                    with os.fdopen(fd2, 'wb') as dst, open(temporary, 'rb') as src:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
                        dst.flush()
                        os.fsync(dst.fileno())
                except BaseException:
                    destination.unlink(missing_ok=True)
                    raise
            return destination
        except (zipfile.BadZipFile, EOFError, RuntimeError) as exc:
            raise VaultError('ZIP entry extraction failed. The archive may be damaged.') from exc
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
