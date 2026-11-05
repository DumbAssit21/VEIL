"""VEIL prototype vault engine. Uses standard cryptographic primitives; not security audited.

Threat model: encrypted contents and filenames remain confidential at rest while
locked. Does not protect from malicious software or administrators controlling the
computer while unlocked, and cannot prevent deletion of encrypted containers.
"""
from __future__ import annotations

import base64
import hashlib
import datetime as dt
import io
import json
import os
import re
import shutil
import secrets
import struct
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VAULT_DIR_NAME = 'VEIL_Vault'
FILE_MAGIC = b'VEILFILE1'
CAPTURE_MAGIC = b'VEILCAP1'
MANIFEST_AAD = b'VEIL-MANIFEST-v1'
MASTER_AAD = b'VEIL-MASTER-v1'
CAM_AAD = b'VEIL-CAMERA-v1'
CHUNK_SIZE = 1024 * 1024


class VaultError(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode('ascii')


def _un64(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.veil-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _atomic_json(path: Path, data: dict) -> None:
    _atomic_bytes(path, json.dumps(data, indent=2, sort_keys=True).encode('utf8'))


def _argon2(password: str, salt: bytes, params: dict) -> bytes:
    return hash_secret_raw(password.encode('utf-8'), salt, time_cost=params['time'],
                           memory_cost=params['memory_kib'], parallelism=params['parallelism'],
                           hash_len=32, type=Type.ID)


def _recovery_bytes(recovery: str) -> bytes:
    cleaned = re.sub(r'[\s-]+', '', recovery).upper()
    try:
        decoded = base64.b32decode(cleaned + '=' * ((8 - len(cleaned) % 8) % 8), casefold=True)
    except Exception as exc:
        raise VaultError('Invalid recovery key.') from exc
    if len(decoded) != 32:
        raise VaultError('Invalid recovery key length.')
    return decoded


def _recovery_kek(raw: bytes, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                info=b'VEIL-RECOVERY-v1').derive(raw)


def _wrap(master: bytes, kek: bytes) -> dict:
    nonce = secrets.token_bytes(12)
    return {'nonce': _b64(nonce), 'data': _b64(AESGCM(kek).encrypt(nonce, master, MASTER_AAD))}


def _unwrap(wrapped: dict, kek: bytes) -> bytes:
    try:
        return AESGCM(kek).decrypt(_un64(wrapped['nonce']), _un64(wrapped['data']), MASTER_AAD)
    except (InvalidTag, ValueError, KeyError) as exc:
        raise VaultError('Incorrect password/recovery key, or damaged vault configuration.') from exc


def _internal_path(value: str) -> str:
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise VaultError('Invalid internal filename.')
    p = PurePosixPath(value)
    if p.is_absolute() or any(part in ('..', '.') for part in value.split('/')) or not p.parts:
        raise VaultError('Invalid internal path.')
    if any(':' in part for part in p.parts):
        raise VaultError('Invalid internal path.')
    return str(p)


class Vault:
    def __init__(self, path: Path, config: dict, master: bytes | None = None,
                 manifest: dict | None = None):
        self.path = Path(path)
        self.config = config
        self.master = master
        self.manifest = manifest

    @staticmethod
    def locate(folder: str | Path) -> Path:
        path = Path(folder).expanduser().resolve()
        if (path / 'vault.json').is_file():
            return path
        if (path / VAULT_DIR_NAME / 'vault.json').is_file():
            return path / VAULT_DIR_NAME
        raise VaultError('No VEIL vault found in that folder.')

    @classmethod
    def create(cls, parent: str | Path, password: str, name: str = VAULT_DIR_NAME) -> tuple['Vault', str]:
        if len(password) < 12:
            raise VaultError('Use a password with at least 12 characters.')
        parent = Path(parent).expanduser().resolve()
        if not parent.exists() or not parent.is_dir():
            raise VaultError('Choose an existing storage folder or drive.')
        if not re.fullmatch(r'VEIL_Vault(?:_[A-Za-z0-9_-]{1,40})?', name):
            raise VaultError('Invalid vault folder name.')
        path = parent / name
        if path.exists():
            raise VaultError('A vault already exists at that location. Open it instead.')
        path.mkdir(mode=0o700)
        (path / 'objects').mkdir()
        (path / 'captures').mkdir()
        master = secrets.token_bytes(32)
        recovery_raw = secrets.token_bytes(32)
        recovery_key = base64.b32encode(recovery_raw).decode('ascii').rstrip('=')
        recovery_key = '-'.join(recovery_key[i:i+4] for i in range(0, len(recovery_key), 4))
        params = {'memory_kib': 64*1024, 'time': 3, 'parallelism': 2}
        salt = secrets.token_bytes(16)
        recovery_salt = secrets.token_bytes(16)
        camera_private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        pub = camera_private.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        priv = camera_private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption())
        config = {
            'format': 'VEIL-v1', 'created_at': _now(), 'capture_enabled': False,
            'capture_public_key': _b64(pub),
            'kdf': params | {'salt': _b64(salt)},
            'password_wrap': _wrap(master, _argon2(password, salt, params)),
            'recovery': {'salt': _b64(recovery_salt),
                         'wrap': _wrap(master, _recovery_kek(recovery_raw, recovery_salt))}
        }
        manifest = {'version': 1, 'items': {}, 'camera_private_key': _b64(priv)}
        obj = cls(path, config, master, manifest)
        obj._save_manifest()
        _atomic_json(path / 'vault.json', config)
        return obj, recovery_key

    @classmethod
    def _load(cls, folder: str | Path) -> 'Vault':
        path = cls.locate(folder)
        try:
            config = json.loads((path / 'vault.json').read_text('utf8'))
            if config['format'] != 'VEIL-v1':
                raise VaultError('Unsupported vault format.')
        except (OSError, ValueError, KeyError) as exc:
            raise VaultError('The vault configuration is invalid or unreadable.') from exc
        return cls(path, config)

    @classmethod
    def open_password(cls, folder: str | Path, password: str) -> 'Vault':
        obj = cls._load(folder)
        params = obj.config['kdf']
        master = _unwrap(obj.config['password_wrap'],
                         _argon2(password, _un64(params['salt']), params))
        obj._load_manifest(master)
        return obj

    @classmethod
    def open_recovery(cls, folder: str | Path, recovery: str) -> 'Vault':
        obj = cls._load(folder)
        block = obj.config['recovery']
        master = _unwrap(block['wrap'],
                         _recovery_kek(_recovery_bytes(recovery), _un64(block['salt'])))
        obj._load_manifest(master)
        return obj

    def _load_manifest(self, master: bytes) -> None:
        try:
            data = (self.path / 'manifest.enc').read_bytes()
            manifest = json.loads(AESGCM(master).decrypt(data[:12], data[12:], MANIFEST_AAD))
            if manifest['version'] != 1 or not isinstance(manifest['items'], dict):
                raise ValueError('Bad manifest format')
        except (OSError, InvalidTag, ValueError, KeyError) as exc:
            raise VaultError('The encrypted vault index is corrupted or cannot be read.') from exc
        self.master = master
        self.manifest = manifest

    def _ensure_unlocked(self):
        if self.master is None or self.manifest is None:
            raise VaultError('Unlock the vault first.')

    def _save_manifest(self) -> None:
        self._ensure_unlocked()
        nonce = secrets.token_bytes(12)
        raw = json.dumps(self.manifest, sort_keys=True).encode('utf8')
        _atomic_bytes(self.path / 'manifest.enc', nonce + AESGCM(self.master).encrypt(nonce, raw, MANIFEST_AAD))

    def _save_config(self) -> None:
        _atomic_json(self.path / 'vault.json', self.config)

    def list_files(self) -> list[dict]:
        self._ensure_unlocked()
        return sorted([dict(file_id=key, **item) for key, item in self.manifest['items'].items()],
                      key=lambda d: d['path'].casefold())

    def _known_folder_paths(self, state: dict | None = None) -> dict[str, str]:
        """Return explicit and implicit folder paths keyed case-insensitively.

        Existing v0.1 manifests do not have the `folders` field; directories
        inferred from historical file paths remain available without migration.
        """
        state = self.manifest if state is None else state
        found: dict[str, str] = {}
        paths = list(state.get('folders', {}).keys())
        paths.extend(item['path'].rsplit('/', 1)[0] for item in state['items'].values()
                     if '/' in item['path'])
        for value in paths:
            pieces = value.split('/')
            for i in range(1, len(pieces) + 1):
                folder = '/'.join(pieces[:i])
                found.setdefault(folder.casefold(), folder)
        return found

    def list_folders(self) -> list[str]:
        self._ensure_unlocked()
        return sorted(self._known_folder_paths().values(), key=str.casefold)

    def _validate_destination(self, path: str, *, ignore_id: str | None = None) -> str:
        self._ensure_unlocked()
        rel = _internal_path(path)
        folders = self._known_folder_paths()
        if rel.casefold() in folders:
            raise VaultError(f'A folder already has the name "{rel}".')
        other_files = {item['path'].casefold() for fid, item in self.manifest['items'].items()
                       if fid != ignore_id}
        if rel.casefold() in other_files:
            raise VaultError(f'A file already has the name "{rel}".')
        # Directories cannot be nested under a file.
        segments = rel.split('/')
        for i in range(1, len(segments)):
            if '/'.join(segments[:i]).casefold() in other_files:
                raise VaultError('A file cannot contain other files or folders.')
        return rel

    def create_folder(self, path: str) -> str:
        """Create an empty folder, represented only inside encrypted manifest."""
        self._ensure_unlocked()
        rel = _internal_path(path)
        folders = self._known_folder_paths()
        if rel.casefold() in folders:
            raise VaultError('A folder with that name already exists.')
        if rel.casefold() in (item['path'].casefold() for item in self.manifest['items'].values()):
            raise VaultError('A file with that name already exists.')
        parent = rel.rsplit('/', 1)[0] if '/' in rel else ''
        if parent and parent.casefold() not in folders:
            raise VaultError('Create the parent folder first.')
        folders_dict = self.manifest.setdefault('folders', {})
        folders_dict[rel] = {'created_at': _now()}
        try:
            self._save_manifest()
        except BaseException:
            folders_dict.pop(rel, None)
            raise
        return rel

    def ensure_folder(self, path: str) -> str:
        """Create the missing parts of a folder path. Useful for folder imports."""
        self._ensure_unlocked()
        if not path:
            return ''
        rel = _internal_path(path)
        parts = rel.split('/')
        for i in range(1, len(parts) + 1):
            partial = '/'.join(parts[:i])
            folders = self._known_folder_paths()
            if partial.casefold() not in folders:
                self.create_folder(partial)
        return rel

    def move_file(self, file_id: str, new_path: str) -> str:
        """Move or rename a file without decrypting/re-encrypting its contents."""
        self._ensure_unlocked()
        if file_id not in self.manifest['items']:
            raise VaultError('File not found.')
        path = self._validate_destination(new_path, ignore_id=file_id)
        parent = path.rsplit('/', 1)[0] if '/' in path else ''
        if parent and parent.casefold() not in self._known_folder_paths():
            raise VaultError('The destination folder does not exist.')
        item = self.manifest['items'][file_id]
        original = item['path']
        if original == path:
            return path
        item['path'] = path
        try:
            self._save_manifest()
        except BaseException:
            item['path'] = original
            raise
        return path

    def move_folder(self, old_path: str, new_path: str) -> str:
        """Move or rename a folder including its descendants atomically in the index."""
        self._ensure_unlocked()
        old = _internal_path(old_path)
        new = _internal_path(new_path)
        known = self._known_folder_paths()
        if old.casefold() not in known:
            raise VaultError('Folder not found.')
        if old == new:
            return new
        if new.casefold().startswith(old.casefold() + '/'):
            raise VaultError('A folder cannot be moved into itself.')
        destination_parent = new.rsplit('/', 1)[0] if '/' in new else ''
        if destination_parent and destination_parent.casefold() not in known:
            raise VaultError('Destination parent folder does not exist.')
        previous = self.manifest
        replacement = json.loads(json.dumps(previous))
        old_lower = old.casefold()
        def updated(value: str) -> str:
            if value.casefold() == old_lower:
                return new
            if value.casefold().startswith(old_lower + '/'):
                return new + value[len(old):]
            return value
        for item in replacement['items'].values():
            item['path'] = updated(item['path'])
        folders = replacement.setdefault('folders', {})
        migrated = {updated(key): value for key, value in folders.items()}
        migrated.setdefault(new, {'created_at': _now()})
        replacement['folders'] = migrated
        # Reject duplicates and file/folder conflicts after the migration.
        seen_files: set[str] = set()
        for item in replacement['items'].values():
            rel = _internal_path(item['path'])
            if rel.casefold() in seen_files:
                raise VaultError('A file with that name already exists in the destination.')
            seen_files.add(rel.casefold())
        folder_names = self._known_folder_paths(replacement)
        if len(folder_names) != len({k.casefold() for k in folder_names}):
            raise VaultError('Folder collision.')
        if any(name in seen_files for name in folder_names):
            raise VaultError('A destination file or folder already exists.')
        # Explicit directories must not be duplicated case-insensitively.
        if len(migrated) != len({key.casefold() for key in migrated}):
            raise VaultError('A destination folder already exists.')
        # Also reject collisions with an *implicit* existing folder after rename.
        unchanged = {key for key in known if key != old_lower and not key.startswith(old_lower + '/')}
        target_paths = [updated(x) for x in known.values() if x.casefold() == old_lower
                        or x.casefold().startswith(old_lower + '/')]
        if any(x.casefold() in unchanged for x in target_paths):
            raise VaultError('Destination folder already exists.')
        self.manifest = replacement
        try:
            self._save_manifest()
        except BaseException:
            self.manifest = previous
            raise
        return new

    def delete_folder(self, path: str, *, recursive: bool = False) -> int:
        """Delete a virtual folder. Recursive deletion permanently deletes files."""
        self._ensure_unlocked()
        rel = _internal_path(path)
        if rel.casefold() not in self._known_folder_paths():
            raise VaultError('Folder not found.')
        def belongs(name: str) -> bool:
            return name.casefold() == rel.casefold() or name.casefold().startswith(rel.casefold() + '/')
        files = [fid for fid, item in self.manifest['items'].items() if belongs(item['path'])]
        child_folders = [name for name in self._known_folder_paths() if belongs(name)]
        if (files or len(child_folders) > 1) and not recursive:
            raise VaultError('Folder is not empty.')
        previous = self.manifest
        replacement = json.loads(json.dumps(previous))
        for fid in files:
            del replacement['items'][fid]
        replacement['folders'] = {key: val for key, val in replacement.get('folders', {}).items()
                                  if not belongs(key)}
        self.manifest = replacement
        try:
            self._save_manifest()
        except BaseException:
            self.manifest = previous
            raise
        for fid in files:
            (self.path / 'objects' / (fid + '.bin')).unlink(missing_ok=True)
        return len(files)

    def _file_key(self, file_id: str) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=32,
                    salt=bytes.fromhex(file_id), info=b'VEIL-FILE-v1').derive(self.master)

    def import_file(self, source: str | Path, internal_path: str | None = None) -> dict:
        self._ensure_unlocked()
        source = Path(source)
        if not source.is_file() or source.is_symlink():
            raise VaultError('Select a regular file (not a symbolic link).')
        rel = _internal_path(internal_path or source.name)
        rel = self._validate_destination(rel)
        # Refuse copying vault ciphertext into itself or importing a live vault object.
        resolved = source.resolve()
        if resolved == self.path or self.path in resolved.parents:
            raise VaultError('Cannot import the vault into itself.')
        file_id = uuid.uuid4().hex
        nonce = secrets.token_bytes(12)
        cipher = Cipher(algorithms.AES(self._file_key(file_id)), modes.GCM(nonce))
        enc = cipher.encryptor()
        enc.authenticate_additional_data(FILE_MAGIC + bytes.fromhex(file_id))
        object_path = self.path / 'objects' / (file_id + '.bin')
        fd, temporary = tempfile.mkstemp(prefix='.import-', dir=object_path.parent)
        size = 0
        try:
            with open(source, 'rb') as inp, os.fdopen(fd, 'wb') as out:
                out.write(FILE_MAGIC + nonce)
                while chunk := inp.read(CHUNK_SIZE):
                    size += len(chunk)
                    out.write(enc.update(chunk))
                out.write(enc.finalize())
                out.write(enc.tag)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, object_path)
            item = {'path': rel, 'size': size, 'imported_at': _now()}
            self.manifest['items'][file_id] = item
            try:
                self._save_manifest()
            except Exception:
                del self.manifest['items'][file_id]
                raise
            return dict(file_id=file_id, **item)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def export_file(self, file_id: str, destination: str | Path) -> Path:
        self._ensure_unlocked()
        if file_id not in self.manifest['items']:
            raise VaultError('The selected file does not exist in the vault.')
        path = self.path / 'objects' / (file_id + '.bin')
        destination = Path(destination)
        if destination.exists():
            raise VaultError('Destination already exists; VEIL will not overwrite it.')
        if not destination.parent.exists():
            raise VaultError('Destination folder does not exist.')
        if self.path == destination or self.path in destination.parents:
            raise VaultError('Do not export unencrypted files into the encrypted vault folder.')
        fd, temporary = tempfile.mkstemp(prefix='.veil-export-', dir=destination.parent)
        try:
            with open(path, 'rb') as inp, os.fdopen(fd, 'wb') as out:
                header = inp.read(len(FILE_MAGIC) + 12)
                if len(header) != len(FILE_MAGIC) + 12 or not header.startswith(FILE_MAGIC):
                    raise VaultError('The encrypted file header is damaged.')
                total = path.stat().st_size
                cipher_len = total - len(FILE_MAGIC) - 12 - 16
                if cipher_len < 0:
                    raise VaultError('The encrypted file is truncated.')
                inp.seek(total - 16)
                tag = inp.read(16)
                inp.seek(len(FILE_MAGIC) + 12)
                decryptor = Cipher(algorithms.AES(self._file_key(file_id)),
                                   modes.GCM(header[-12:], tag)).decryptor()
                decryptor.authenticate_additional_data(FILE_MAGIC + bytes.fromhex(file_id))
                remaining = cipher_len
                while remaining:
                    chunk = inp.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise VaultError('The encrypted file is truncated.')
                    out.write(decryptor.update(chunk))
                    remaining -= len(chunk)
                try:
                    out.write(decryptor.finalize())
                except InvalidTag as exc:
                    raise VaultError('Integrity check failed. The file may be corrupted or altered.') from exc
                out.flush()
                os.fsync(out.fileno())
            if destination.exists():
                raise VaultError('Destination was created during export; refusing to overwrite.')
            # Create destination with O_EXCL to prevent a concurrent replacement.
            # This temporary plaintext is only published after authentication passes.
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise VaultError('Destination already exists.') from exc
            except OSError:
                # FAT/exFAT and some network filesystems do not support hard links.
                # Fall back to a no-overwrite creation after decrypt/auth succeeds.
                try:
                    fd_out = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError as exc:
                    raise VaultError('Destination already exists.') from exc
                try:
                    with os.fdopen(fd_out, 'wb') as dest_out, open(temporary, 'rb') as src_in:
                        shutil.copyfileobj(src_in, dest_out, CHUNK_SIZE)
                        dest_out.flush()
                        os.fsync(dest_out.fileno())
                except BaseException:
                    destination.unlink(missing_ok=True)
                    raise
            return destination
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def read_small_file(self, file_id: str, limit: int = 20 * 1024 * 1024) -> bytes:
        """Decrypt a small file into memory for an in-app preview; never to disk."""
        self._ensure_unlocked()
        item = self.manifest['items'].get(file_id)
        if item is None:
            raise VaultError('File not found.')
        if item['size'] > limit:
            raise VaultError('File is too large for an in-app preview.')
        blob = (self.path / 'objects' / (file_id + '.bin')).read_bytes()
        if not blob.startswith(FILE_MAGIC) or len(blob) < len(FILE_MAGIC) + 12 + 16:
            raise VaultError('Damaged encrypted file.')
        nonce = blob[len(FILE_MAGIC):len(FILE_MAGIC) + 12]
        try:
            return AESGCM(self._file_key(file_id)).decrypt(
                nonce, blob[len(FILE_MAGIC) + 12:], FILE_MAGIC + bytes.fromhex(file_id))
        except InvalidTag as exc:
            raise VaultError('Encrypted file integrity check failed.') from exc

    def read_verified_memory(self, file_id: str, limit: int = 768 * 1024 * 1024) -> io.BytesIO:
        """Authenticate then return a file-like plaintext buffer kept in memory.

        Primarily for viewing ZIP archives in-app. No plaintext file is created.
        Callers MUST close the returned buffer when done; it can be large and its
        contents remain in process memory while the vault is unlocked.
        """
        self._ensure_unlocked()
        item = self.manifest['items'].get(file_id)
        if item is None:
            raise VaultError('The selected file does not exist in the vault.')
        if item['size'] > limit:
            raise VaultError('This archive is too large to browse safely in memory '
                             f'(limit: {limit // (1024 * 1024)} MiB). You can export it instead.')
        path = self.path / 'objects' / (file_id + '.bin')
        result = io.BytesIO()
        try:
            with open(path, 'rb') as inp:
                header = inp.read(len(FILE_MAGIC) + 12)
                if len(header) != len(FILE_MAGIC) + 12 or not header.startswith(FILE_MAGIC):
                    raise VaultError('The encrypted file header is damaged.')
                total = os.fstat(inp.fileno()).st_size
                cipher_len = total - len(FILE_MAGIC) - 12 - 16
                if cipher_len < 0 or cipher_len != item['size']:
                    raise VaultError('The encrypted file is damaged or truncated.')
                inp.seek(total - 16)
                tag = inp.read(16)
                if len(tag) != 16:
                    raise VaultError('The encrypted file is truncated.')
                inp.seek(len(FILE_MAGIC) + 12)
                dec = Cipher(algorithms.AES(self._file_key(file_id)),
                             modes.GCM(header[-12:], tag)).decryptor()
                dec.authenticate_additional_data(FILE_MAGIC + bytes.fromhex(file_id))
                remaining = cipher_len
                while remaining:
                    encrypted = inp.read(min(CHUNK_SIZE, remaining))
                    if not encrypted:
                        raise VaultError('The encrypted file is truncated.')
                    result.write(dec.update(encrypted))
                    remaining -= len(encrypted)
                try:
                    result.write(dec.finalize())
                except InvalidTag as exc:
                    raise VaultError('Integrity check failed; encrypted file may be altered.') from exc
            result.seek(0)
            return result
        except BaseException:
            result.close()
            raise

    def delete_file(self, file_id: str) -> None:
        self._ensure_unlocked()
        if file_id not in self.manifest['items']:
            raise VaultError('File not found.')
        item = self.manifest['items'].pop(file_id)
        try:
            self._save_manifest()
        except Exception:
            self.manifest['items'][file_id] = item
            raise
        (self.path / 'objects' / (file_id + '.bin')).unlink(missing_ok=True)

    def change_password(self, new_password: str) -> None:
        self._ensure_unlocked()
        if len(new_password) < 12:
            raise VaultError('Use a password with at least 12 characters.')
        salt = secrets.token_bytes(16)
        config = json.loads(json.dumps(self.config))
        config['kdf']['salt'] = _b64(salt)
        config['password_wrap'] = _wrap(self.master, _argon2(new_password, salt, config['kdf']))
        _atomic_json(self.path / 'vault.json', config)
        self.config = config

    def set_camera_enabled(self, enabled: bool) -> None:
        self._ensure_unlocked()
        config = json.loads(json.dumps(self.config))
        config['capture_enabled'] = bool(enabled)
        _atomic_json(self.path / 'vault.json', config)
        self.config = config

    def camera_entries(self) -> list[dict]:
        self._ensure_unlocked()
        entries = []
        for path in (self.path / 'captures').glob('*.cap'):
            try:
                data = json.loads(path.read_text('utf8'))
                entries.append({'id': path.stem, 'captured_at': data['captured_at']})
            except (OSError, ValueError, KeyError):
                continue
        return sorted(entries, key=lambda d: d['captured_at'], reverse=True)

    def open_camera_image(self, capture_id: str) -> bytes:
        self._ensure_unlocked()
        if not re.fullmatch(r'[0-9a-f]{32}', capture_id):
            raise VaultError('Invalid snapshot ID.')
        try:
            obj = json.loads((self.path / 'captures' / (capture_id + '.cap')).read_text('utf8'))
            private_pem = _un64(self.manifest['camera_private_key'])
            private_key = serialization.load_pem_private_key(private_pem, password=None)
            key = private_key.decrypt(_un64(obj['wrapped_key']),
                                      padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                                                   algorithm=hashes.SHA256(), label=None))
            return AESGCM(key).decrypt(_un64(obj['nonce']), _un64(obj['ciphertext']),
                                       CAM_AAD + obj['captured_at'].encode('ascii'))
        except (ValueError, InvalidTag, OSError, KeyError) as exc:
            raise VaultError('Could not decrypt this capture.') from exc

    def delete_camera_image(self, capture_id: str) -> None:
        self._ensure_unlocked()
        if not re.fullmatch(r'[0-9a-f]{32}', capture_id):
            raise VaultError('Invalid snapshot ID.')
        (self.path / 'captures' / (capture_id + '.cap')).unlink(missing_ok=True)

    # Additional features keep all private metadata in the existing encrypted manifest.
    # Deleted file objects remain encrypted and are not exposed by the regular file browser.
    def trash_file(self, file_id: str) -> None:
        self._ensure_unlocked()
        if file_id not in self.manifest['items']:
            raise VaultError('File not found.')
        replacement = json.loads(json.dumps(self.manifest))
        item = replacement['items'].pop(file_id)
        replacement.setdefault('trash', {})[file_id] = dict(item, deleted_at=_now())
        self._commit(replacement)
        self.log_activity('file_trashed', item['path'])

    def trash_folder(self, folder: str) -> int:
        self._ensure_unlocked()
        folder = _internal_path(folder)
        if folder.casefold() not in self._known_folder_paths():
            raise VaultError('Folder does not exist.')
        def under(path):
            return path.casefold() == folder.casefold() or path.casefold().startswith(folder.casefold() + '/')
        replacement = json.loads(json.dumps(self.manifest))
        moved = 0
        for fid, item in list(replacement['items'].items()):
            if under(item['path']):
                replacement.setdefault('trash', {})[fid] = dict(replacement['items'].pop(fid), deleted_at=_now())
                moved += 1
        removed = {name: replacement.get('folders', {}).get(name, {'created_at': _now()})
                   for name in self._known_folder_paths().values() if under(name)}
        replacement['folders'] = {k: v for k, v in replacement.get('folders', {}).items() if not under(k)}
        # Empty folders are preserved in Trash and can be restored independently.
        replacement.setdefault('trash_folders', {}).update(removed or {folder: {'created_at': _now()}})
        self._commit(replacement)
        self.log_activity('folder_trashed', folder)
        return moved

    def trash_entries(self) -> list[dict]:
        self._ensure_unlocked()
        files = [dict(id=fid, kind='file', **entry) for fid, entry in self.manifest.get('trash', {}).items()]
        folders = [dict(id='dir:'+name, path=name, kind='folder', deleted_at='', size=None)
                   for name in self.manifest.get('trash_folders', {})]
        return sorted(files + folders, key=lambda e: e['path'].casefold())

    def restore_trash(self, ids: list[str]) -> int:
        self._ensure_unlocked()
        replacement = json.loads(json.dumps(self.manifest))
        restored = 0
        # Do all checks before committing so a conflicting batch changes nothing.
        for fid in ids:
            if fid.startswith('dir:'):
                folder = fid[4:]
                if folder not in replacement.get('trash_folders', {}):
                    raise VaultError('Trashed folder missing.')
                if folder.casefold() in self._known_folder_paths(replacement):
                    raise VaultError('Folder name already exists: ' + folder)
                parent = folder.rsplit('/', 1)[0] if '/' in folder else ''
                if parent and parent.casefold() not in self._known_folder_paths(replacement):
                    raise VaultError('Restore parent folders first: ' + parent)
                replacement.setdefault('folders', {})[folder] = replacement['trash_folders'].pop(folder)
            else:
                entry = replacement.get('trash', {}).get(fid)
                if entry is None:
                    raise VaultError('Trashed file missing.')
                newpath = entry['path']
                if any(x['path'].casefold() == newpath.casefold() for x in replacement['items'].values()):
                    raise VaultError('Existing file has the same name: ' + newpath)
                if newpath.casefold() in self._known_folder_paths(replacement):
                    raise VaultError('Folder has the same name: ' + newpath)
                parent = newpath.rsplit('/', 1)[0] if '/' in newpath else ''
                if parent and parent.casefold() not in self._known_folder_paths(replacement):
                    raise VaultError('Restore its parent folder first: ' + parent)
                entry = replacement['trash'].pop(fid)
                entry.pop('deleted_at', None)
                replacement['items'][fid] = entry
            restored += 1
        self._commit(replacement)
        self.log_activity('trash_restored', str(restored))
        return restored

    def purge_trash(self, ids: list[str]) -> int:
        self._ensure_unlocked()
        replacement = json.loads(json.dumps(self.manifest))
        delete_objects = []
        for fid in ids:
            if fid.startswith('dir:'):
                if fid[4:] not in replacement.get('trash_folders', {}):
                    raise VaultError('Trashed folder missing.')
                replacement['trash_folders'].pop(fid[4:])
            else:
                if fid not in replacement.get('trash', {}):
                    raise VaultError('Trashed file missing.')
                replacement['trash'].pop(fid)
                delete_objects.append(fid)
        self._commit(replacement)
        for fid in delete_objects:
            (self.path / 'objects' / (fid + '.bin')).unlink(missing_ok=True)
        self.log_activity('trash_permanently_deleted', str(len(ids)))
        return len(ids)

    def _commit(self, replacement: dict) -> None:
        previous = self.manifest
        self.manifest = replacement
        try:
            self._save_manifest()
        except BaseException:
            self.manifest = previous
            raise

    def move_many(self, ids: list[str], destination: str) -> int:
        """Validate/commit an entire bulk move as a single encrypted-index transaction."""
        self._ensure_unlocked()
        target = _internal_path(destination) if destination else ''
        if target and target.casefold() not in self._known_folder_paths():
            raise VaultError('Destination folder does not exist.')
        replacement = json.loads(json.dumps(self.manifest))
        if len(ids) != len(set(ids)):
            raise VaultError('Duplicate selection.')
        def redirect(path):
            return (target + '/' if target else '') + path.rsplit('/', 1)[-1]
        selected_folders = [fid[4:] for fid in ids if fid.startswith('dir:')]
        # If a parent folder was selected, ignore its selected descendants.
        selected_folders = [name for name in selected_folders if not any(
            name.casefold().startswith(p.casefold() + '/') for p in selected_folders if p != name)]
        effective_files = [fid for fid in ids if not fid.startswith('dir:') and
                           not any(self.manifest['items'].get(fid, {}).get('path', '').casefold().startswith(
                               folder.casefold() + '/') for folder in selected_folders)]
        for folder in selected_folders:
            if folder.casefold() not in self._known_folder_paths():
                raise VaultError('Selected folder not found.')
            if target.casefold() == folder.casefold() or target.casefold().startswith(folder.casefold() + '/'):
                raise VaultError('Cannot move a folder inside itself.')
        for fid in effective_files:
            if fid not in replacement['items']:
                raise VaultError('Selected file not found.')
            replacement['items'][fid]['path'] = redirect(replacement['items'][fid]['path'])
        for folder in selected_folders:
            renamed = redirect(folder)
            def rewrite(path):
                if path.casefold() == folder.casefold():
                    return renamed
                if path.casefold().startswith(folder.casefold() + '/'):
                    return renamed + path[len(folder):]
                return path
            for item in replacement['items'].values():
                item['path'] = rewrite(item['path'])
            replacement['folders'] = {rewrite(path): value for path, value in replacement.get('folders', {}).items()}
            replacement['folders'].setdefault(renamed, {'created_at': _now()})
        names = [v['path'].casefold() for v in replacement['items'].values()]
        if len(names) != len(set(names)):
            raise VaultError('Bulk move would produce duplicate filenames.')
        known = self._known_folder_paths(replacement)
        explicit = replacement.get('folders', {})
        if len(explicit) != len({name.casefold() for name in explicit}):
            raise VaultError('Bulk move would produce duplicate folders.')
        if any(name in known for name in names):
            raise VaultError('Bulk move would conflict with a folder.')
        if any('/'.join(path.split('/')[:i]).casefold() in names for path in names
               for i in range(1, len(path.split('/')))):
            raise VaultError('Cannot place a file inside another file.')
        self._commit(replacement)
        self.log_activity('bulk_move', str(len(ids)))
        return len(ids)

    def trash_many(self, ids: list[str]) -> int:
        self._ensure_unlocked()
        if len(ids) != len(set(ids)):
            raise VaultError('Duplicate selection.')
        replacement = json.loads(json.dumps(self.manifest))
        for fid in ids:
            if fid.startswith('dir:'):
                name = fid[4:]
                if name.casefold() not in self._known_folder_paths():
                    raise VaultError('Selected folder not found.')
                def under(path):
                    return path.casefold() == name.casefold() or path.casefold().startswith(name.casefold() + '/')
                for key, value in list(replacement['items'].items()):
                    if under(value['path']):
                        replacement.setdefault('trash', {})[key] = dict(replacement['items'].pop(key), deleted_at=_now())
                # Include implicit folders inferred from existing file paths too.
                for path in self._known_folder_paths().values():
                    if under(path):
                        replacement.setdefault('trash_folders', {}).setdefault(
                            path, replacement.get('folders', {}).get(path, {'created_at': _now()}))
                for path in list(replacement.get('folders', {})):
                    if under(path):
                        replacement['folders'].pop(path)
                replacement.setdefault('trash_folders', {}).setdefault(name, {'created_at': _now()})
            else:
                item = replacement['items'].pop(fid, None)
                if item is None:
                    # A file may be in a selected parent folder; skip it in that case.
                    if fid in replacement.get('trash', {}):
                        continue
                    raise VaultError('Selected file not found.')
                replacement.setdefault('trash', {})[fid] = dict(item, deleted_at=_now())
        self._commit(replacement)
        self.log_activity('bulk_trashed', str(len(ids)))
        return len(ids)

    def set_preferences(self, *, idle_minutes=None, lock_windows=None, lock_sleep=None, backup_folder=None) -> None:
        self._ensure_unlocked()
        replacement = json.loads(json.dumps(self.manifest))
        pref = replacement.setdefault('preferences', {})
        if idle_minutes is not None:
            minutes = int(idle_minutes)
            if minutes not in (0, 1, 5, 10, 15, 30, 60):
                raise VaultError('Unsupported auto-lock timeout.')
            pref['idle_minutes'] = minutes
        if lock_windows is not None:
            pref['lock_windows'] = bool(lock_windows)
        if lock_sleep is not None:
            pref['lock_sleep'] = bool(lock_sleep)
        if backup_folder is not None:
            pref['backup_folder'] = str(backup_folder)
        self._commit(replacement)

    def set_backup_schedule(self, hours: int = 0, last_at: str | None = None) -> None:
        self._ensure_unlocked()
        if hours not in (0, 24, 168):
            raise VaultError('Choose off, daily, or weekly backup.')
        replacement = json.loads(json.dumps(self.manifest))
        replacement.setdefault('preferences', {})['backup_hours'] = hours
        if last_at is not None:
            replacement['preferences']['last_backup_utc'] = last_at
        self._commit(replacement)

    def preferences(self) -> dict:
        self._ensure_unlocked()
        return dict({'idle_minutes': 10, 'lock_windows': True, 'lock_sleep': True},
                    **self.manifest.get('preferences', {}))

    def log_activity(self, action: str, detail: str = '') -> None:
        self._ensure_unlocked()
        if action not in ('unlock', 'import', 'export', 'file_trashed', 'folder_trashed',
                          'trash_restored', 'trash_permanently_deleted', 'bulk_move',
                          'bulk_trashed', 'backup', 'health_check', 'password_changed', 'camera_changed'):
            raise VaultError('Unknown security event.')
        replacement = json.loads(json.dumps(self.manifest))
        events = replacement.setdefault('activity', [])
        events.append({'at': _now(), 'event': action, 'detail': str(detail)[:160]})
        replacement['activity'] = events[-2000:]
        self._commit(replacement)

    def activity(self) -> list[dict]:
        self._ensure_unlocked()
        events = list(self.manifest.get('activity', []))
        for path in (self.path / 'events').glob('*.evt'):
            try:
                encoded = json.loads(path.read_text('utf8'))
                priv = serialization.load_pem_private_key(_un64(self.manifest['camera_private_key']), None)
                key = priv.decrypt(_un64(encoded['wrapped_key']), padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
                plaintext = AESGCM(key).decrypt(_un64(encoded['nonce']), _un64(encoded['ciphertext']),
                                                 b'VEIL-EVENT-v1')
                events.append(json.loads(plaintext))
            except Exception:
                events.append({'at': '?', 'event': 'unreadable_event', 'detail': path.name})
        return sorted(events, key=lambda e: e['at'], reverse=True)

    def backup_to(self, destination_parent: str | Path) -> Path:
        """Copy only ciphertext and vault metadata; hashes are verified after the copy.

        The caller must ensure no other writers are active during this snapshot.
        """
        self._ensure_unlocked()
        parent = Path(destination_parent).expanduser().resolve()
        if not parent.is_dir() or parent == self.path or parent in self.path.parents or self.path in parent.parents:
            raise VaultError('Choose an existing backup folder outside the vault and its parent drive root.')
        name = 'VEIL-Backup-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3)
        target = parent / name
        try:
            if any(path.is_symlink() for path in self.path.rglob('*')):
                raise VaultError('Vault contains a symbolic link; refusing to follow it during backup.')
            shutil.copytree(self.path, target, symlinks=False)
            for src in self.path.rglob('*'):
                if src.is_file():
                    relative = src.relative_to(self.path)
                    dest = target / relative
                    if src.stat().st_size != dest.stat().st_size:
                        raise VaultError('Backup size verification failed: ' + str(relative))
                    def digest(path):
                        h = hashlib.sha256()
                        with path.open('rb') as stream:
                            for block in iter(lambda: stream.read(CHUNK_SIZE), b''):
                                h.update(block)
                        return h.digest()
                    if digest(src) != digest(dest):
                        raise VaultError('Backup verification failed: ' + str(relative))
            self.log_activity('backup', name)
            return target
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise

    @staticmethod
    def restore_backup_copy(source_folder: str | Path, destination_parent: str | Path) -> Path:
        """Non-destructively clone a complete ciphertext backup to a new folder."""
        source = Vault.locate(source_folder)
        parent = Path(destination_parent).expanduser().resolve()
        if not parent.is_dir() or parent == source or source in parent.parents:
            raise VaultError('Choose an existing destination outside the backup source.')
        dest = parent / ('VEIL_Restored_' + dt.datetime.now().strftime('%Y%m%d-%H%M%S')
                         + '-' + secrets.token_hex(3))
        try:
            if any(f.is_symlink() for f in source.rglob('*')):
                raise VaultError('Backup contains symbolic links; restore refused.')
            shutil.copytree(source, dest)
            for current in source.rglob('*'):
                if current.is_file():
                    original = current.relative_to(source)
                    output = dest / original
                    def digest(path):
                        h = hashlib.sha256()
                        with path.open('rb') as fd:
                            for data in iter(lambda: fd.read(CHUNK_SIZE), b''):
                                h.update(data)
                        return h.digest()
                    if digest(current) != digest(output):
                        raise VaultError('Restore verification failed: ' + str(original))
            Vault._load(dest)  # Format/config check; password test happens at login.
            return dest
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)
            raise

    def health_check(self) -> dict:
        """Authenticate encrypted data in a streaming fashion without exporting plaintext."""
        self._ensure_unlocked()
        errors = []
        items = dict(self.manifest['items']) | dict(self.manifest.get('trash', {}))
        for fid, item in items.items():
            obj = self.path / 'objects' / (fid + '.bin')
            try:
                with obj.open('rb') as stream:
                    header = stream.read(len(FILE_MAGIC) + 12)
                    size = obj.stat().st_size
                    if len(header) != len(FILE_MAGIC) + 12 or not header.startswith(FILE_MAGIC) or size != len(header) + item['size'] + 16:
                        raise VaultError('Bad header or file length')
                    stream.seek(-16, 2)
                    tag = stream.read(16)
                    stream.seek(len(header))
                    decoder = Cipher(algorithms.AES(self._file_key(fid)), modes.GCM(header[-12:], tag)).decryptor()
                    decoder.authenticate_additional_data(FILE_MAGIC + bytes.fromhex(fid))
                    remaining = item['size']
                    while remaining:
                        chunk = stream.read(min(CHUNK_SIZE, remaining))
                        if not chunk:
                            raise VaultError('Truncated object')
                        decoder.update(chunk)
                        remaining -= len(chunk)
                    decoder.finalize()
            except (OSError, InvalidTag, VaultError, ValueError) as exc:
                errors.append({'file': item['path'], 'error': type(exc).__name__})
        expected = set(items)
        orphan_count = sum(1 for p in (self.path / 'objects').glob('*.bin') if p.stem not in expected)
        capture_errors = []
        captures = list((self.path / 'captures').glob('*.cap'))
        for capture in captures:
            try:
                self.open_camera_image(capture.stem)
            except Exception as exc:
                capture_errors.append({'capture': capture.stem, 'error': type(exc).__name__})
        return {'checked': len(items), 'errors': errors, 'orphans': orphan_count,
                'captures': len(captures), 'capture_errors': capture_errors}

    def lock(self):
        self.master = None
        self.manifest = None


def record_login_event(folder: str | Path, success: bool) -> None:
    """Capture an encrypted authentication result without needing an unlocked vault."""
    vault = Vault._load(folder)
    pub = serialization.load_der_public_key(_un64(vault.config['capture_public_key']))
    key = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    entry = {'at': _now(), 'event': 'unlock_success' if success else 'unlock_failure', 'detail': ''}
    wrapped = pub.encrypt(key, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                                           algorithm=hashes.SHA256(), label=None))
    encrypted = AESGCM(key).encrypt(nonce, json.dumps(entry).encode('utf8'), b'VEIL-EVENT-v1')
    folder = vault.path / 'events'
    folder.mkdir(exist_ok=True)
    _atomic_json(folder / (uuid.uuid4().hex + '.evt'), {'nonce': _b64(nonce),
                  'wrapped_key': _b64(wrapped), 'ciphertext': _b64(encrypted)})


def record_camera_snapshot(folder: str | Path, jpeg_data: bytes) -> str:
    """Encrypt webcam bytes with *public* key, so capture works while vault is locked.

    The camera is only activated by the UI after explicit opt-in.
    """
    vault = Vault._load(folder)
    if not vault.config.get('capture_enabled'):
        raise VaultError('Camera snapshots are not enabled.')
    if not jpeg_data or len(jpeg_data) > 15 * 1024 * 1024:
        raise VaultError('Invalid camera image size.')
    pub = serialization.load_der_public_key(_un64(vault.config['capture_public_key']))
    key = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    at = _now()
    encoded = {
        'format': 'VEIL-CAMERA-v1', 'captured_at': at,
        'nonce': _b64(nonce),
        'wrapped_key': _b64(pub.encrypt(key, padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(), label=None))),
        'ciphertext': _b64(AESGCM(key).encrypt(nonce, jpeg_data, CAM_AAD + at.encode('ascii')))
    }
    name = uuid.uuid4().hex
    _atomic_json(vault.path / 'captures' / (name + '.cap'), encoded)
    return name
