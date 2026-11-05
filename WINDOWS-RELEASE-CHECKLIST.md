# Windows preview validation checklist (before publishing GitHub draft)

## Build verification

- [ ] GitHub Actions Windows build and Python unit tests succeed.
- [ ] Installer downloads from the GitHub draft release and SHA-256 hash matches its `.sha256` file.
- [ ] Windows installer installs VEIL in user LocalAppData Programs, registers Start Menu link, optionally desktop link.
- [ ] Installer is unsigned, so users see an accurate warning. No claims of independent audit.
- [ ] Close app, run a newer installer, verify launch and existing vault discovery.
- [ ] Uninstall does not delete or alter an independently located `VEIL_Vault` on another drive.

## Vault and recovery verification (test vault only)

- [ ] Create test vault on external drive and record recovery key offline.
- [ ] Add files, full folders, a photo, video and ZIP; verify names not plaintext in Explorer.
- [ ] Lock and unlock from password; wrong password cannot open vault.
- [ ] Recover using key and change password; old password no longer works.
- [ ] Drag-select/Ctrl-click/Shift-click several files and bulk move.
- [ ] Bulk delete to encrypted Recycle Bin, restore, and verify SHA-256 of exported originals.
- [ ] Run health check and create + restore encrypted backup into a **new** folder.
- [ ] Opt-in webcam capture, view after unlock, then disable; never upload.
- [ ] Browse ZIP and play photo/video previews; verify lock closes windows.
- [ ] Use Settings manual update check; opt-in check only sends request when configured.

## Security gates before general release

- [ ] External crypto/security review, crash/interrupted export tests, and transactional multi-process lock.
- [ ] Cross-PC and Windows sleep/screen-lock validation.
- [ ] Code signing and safe signed update strategy.

Until these gates are complete: use *draft prerelease*, keep explicit experimental warning, and do not recommend VEIL for irreplaceable files.
