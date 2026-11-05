# VEIL — encrypted personal vault for Windows

> **EXPERIMENTAL PREVIEW — NOT SECURITY-AUDITED.** Do not use as the only copy of any important files.

VEIL is a local-first Windows encrypted vault supporting files, folders, photos, video, ZIP browsing, an opt-in encrypted login webcam capture, recovery keys, a recycle bin, backups, multiple vaults, and security activity history.

## Privacy and security

VEIL stores encrypted contents and filenames in a folder on the drive selected by the user. The folder is visible in Windows Explorer, but the protected data is not readable without the vault key. It cannot prevent deletion or copying of ciphertext. VEIL does **not** securely erase imported originals, operating system caches, temporary plaintext export files, or SSD remnants.

VEIL is an educational/experimental app. Its encryption uses established libraries but the application has **not** received an independent security audit. See [SECURITY.md](SECURITY.md) for important known risks. Camera captures are opt-in and encrypted locally; they are not face recognition.

## Install (source version)

1. Install Python 3.12+ on Windows.
2. Extract the source outside your existing vault folder.
3. Run `Start-VEIL.bat` to install dependencies and launch.
4. Use **Open existing vault** or create a test vault.

**Never delete or overwrite the existing `VEIL_Vault` on your selected drive when updating the app.** Create a complete backup first and keep your recovery key offline.

## Windows installer (unsigned beta preview)

This repository contains an automated Windows build: PyInstaller + Inno Setup 6. The build creates `VEIL-Setup-v0.10.0-beta.1.exe` and a SHA-256 checksum file, which are published in a *draft prerelease* after pushing the matching tag. Building Windows binaries requires a Windows runner; this source ZIP does not contain a Windows EXE. The installer writes program files under the user's local app programs directory and **does not remove external vault directories on uninstall**.

Build on Windows with Python 3, Inno Setup 6, and PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Build-Windows-Release.ps1
```

Run only on a trusted Windows machine. The GitHub CI configuration runs unit tests before packaging. No code signing is supplied, so Windows may warn about an unknown publisher.

## GitHub publication

The repository should be named `DumbAssit21/VEIL`. On your Windows PC, with GitHub CLI and Git for Windows already installed and authenticated, extract this source ZIP and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Publish-VEIL-To-GitHub.ps1
```

This creates the public GitHub repository if missing, commits and pushes **source code only**, and pushes the beta tag to trigger the Windows installer workflow. The release is created as a **draft prerelease** so that you can inspect it before making a public download available.

## Updates

Settings has a manual **Check for updates** button and an optional startup check. It reads public releases from GitHub and offers to open the official Releases page. No executable is silently downloaded or installed. The automatic update *installation* mechanism is intentionally deferred until signed releases can be validated reliably.

## Tests

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py' -v
```

The unit tests use disposable test vaults; they never modify your actual encrypted vault. The build must additionally pass real-Windows camera, video, file-import, restore, ZIP, and upgrade smoke tests before wider distribution.

## Licensing

Source is publicly viewable on GitHub when published. **No license is granted by default.** The repository owner can select an open-source or source-available license separately; publishing source code does not automatically mean others may redistribute or modify it.
