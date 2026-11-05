## v0.10.0-beta.1 — Github preview build & updater

- Public GitHub repository bootstrap, Windows installer build scripts, CI workflows and draft release artifacts.
- Opt-in GitHub release checking; no automatic software execution or download.
- No change to encrypted storage, compatibility or file format.

## v0.9.0

- Add multi-select for Details/List and icon tiles, mouse-drag group selection, and batch moves.
- Add encrypted recycle bin (soft-delete/restore/empty) and preserve nested implicit folders.
- Add Gallery, Activity, and Settings enhancements to sidebar.
- Add configurable auto-lock and best-effort Windows session/sleep detection.
- Add manual and scheduled encrypted backups, checksum validation, and non-destructive restore copy.
- Add streaming vault/camera integrity checking, and independent named vaults with recent-vault chooser.
- Preserve format VEIL-v1; document security/testing limitations and postpone insecure Windows Hello shortcuts.

# VEIL v0.8 — Dark scrollbar and Windows styling polish

- Replaced light native scrollbars with custom dark scrollbars in the Files views, Security Photos, ZIP browser, and text preview.
- Tuned notebook, file list, view selector, and sidebar control styling to avoid bright outlines on the dark theme.
- Kept all seven views, stored view preference, vault functionality, ZIP browsing, videos, webcam security photos, and login/recovery behavior.
- Added a GUI regression test for dark scrolling in Details, Medium icons, Extra large icons, and Tiles.
- No changes to the encrypted storage format or `veil/core.py`. Back up your vault before trying the new application build.
- Tested under Linux virtual display; specific Windows scrollbar/input behavior needs verification.

---

# VEIL v0.7 — Premium UI redesign

- Rebuilt the unlocked Windows window to match the approved three-panel design: left navigation, file manager, right details/preview inspector.
- New diamond logo, navy/blue visual system, modern toolbar, file-type icons, storage meter, search, breadcrumbs, and lock control.
- Sidebar navigation for Files, Security Photos, Settings; original camera and settings controls retained.
- Search filenames/paths across vault folders while unlocked; clear the filter to navigate folders normally.
- Existing seven viewing modes and in-memory image thumbnails retained.
- Selected photos preview directly in the right panel; native memory video preview is now supported outside ZIPs too (media dependencies/limits apply).
- New branded unlock screen and Windows icon for source launch and PyInstaller EXE.
- `veil/core.py`, encrypted on-disk format, passwords, recovery keys, and webcam capture logic unchanged; backward-compatible with v0.1–v0.6 vaults.
- 28 core automated tests pass; GUI view, import, ZIP, camera, video wiring, and new sidebar/search/inspector smoke tests pass in a virtual display. Actual Windows media playback and drag/drop remain to be tested.

---

# VEIL v0.6 — In-memory ZIP video previews

- Open `.mp4`, `.m4v`, `.mov`, `.mkv`, `.webm`, and `.avi` ZIP members in a read-only in-app video player. PyAV/FFmpeg decodes directly from bytes after authenticated decryption in RAM; no extracted video is written to disk by VEIL.
- Play/Pause, Replay, elapsed time, optional audio output through sounddevice, and automatic media shutdown on vault lock/ZIP browser close.
- Limit video preview members to 160 MiB and reject excessive decoded frame dimensions. Existing 12 MiB image/text previews and 768 MiB ZIP browser limit are unchanged.
- 28 unit tests passing and ZIP video UI wiring smoke test passing. Real video decoding/sound on Windows remains to be verified (PyAV not installed in the development container).
- No changes to stored vault layout, encryption, recovery, password, settings, or webcam photo formats.

---

# VEIL changelog

## v0.5.0

- Double-click encrypted ZIP archives to browse their nested folder structure **inside VEIL**.
- Add a **Preview / Browse ZIP** action and right-click **Browse ZIP**.
- Authenticate and decrypt ZIP data into RAM before exposing member information; no automatic plaintext extraction to disk.
- Add read-only previews of small images and text files from within ZIP archives.
- Add explicit, no-overwrite **Export selected file** for individual ZIP members.
- Filter unsafe member paths and symbolic links; set archive (768 MiB), member preview (12 MiB), and export size limits.
- Close ZIP browser and release in-memory ZIP buffers when a vault is locked.
- Add seven ZIP security/compatibility tests and a desktop ZIP-browser smoke test.
- Preserve v0.1–v0.4 vault format, original ZIP contents, password, recovery key, and webcam snapshots.

## v0.4.0

- Add a dedicated **Add ZIP** button in the Files tab, alongside Add files and Import folder.
- ZIP archives are imported **as-is**, encrypted as one file without extracting, unpacking, or changing contents.
- ZIP archives are also supported using Add files and drag-and-drop; preserve their original filenames and folder placement within the encrypted manifest.
- Add ZIP roundtrip and GUI routing tests; encryption format, vault data, passwords, recovery keys, and snapshots are unchanged.


## v0.3.0

- Fix inaccessible Security photos actions on Windows at typical 720p window sizes.
- Move photo View/Delete/Refresh controls above the snapshot list so they remain visible.
- Add an immediate encrypted-photo preview within the Security photos tab when a timestamp is selected.
- Double-click a timestamp or press Enter to open the full-size photo viewer.
- Restore the selection on refresh; auto-select the newest capture when opening the tab.
- Clear in-tab previews and close expanded photos on vault lock/exit.
- Add a dedicated GUI regression test for camera photo viewing and window layout.
- No modifications to the encryption format, existing captures, passwords, recovery keys, or vault layout.

## v0.2.0

- Drag-and-drop files/folders from Windows Explorer into the Files tab.
- Import directly into private folders, including preserving empty source directories.
- Add seven view modes, in-memory image thumbnails and remembered view preference.
- Create private folders, navigate, rename, move, export and delete, including nested directories.
- Backwards-compatible support for v0.1 manifests, with no new master/recovery keys.
- Make imports asynchronous; show status and handle naming collisions without replacing items.
- Automatically close preview windows when the vault locks.
- Add seven folder/index/integrity tests and headless UI smoke scripts.

## v0.1.0

- Basic encrypted vault and recovery-key workflow.
- Import/export, image preview and opt-in encrypted webcam login snapshots.
