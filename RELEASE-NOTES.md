# VEIL v0.10.0-beta.1 — Public Preview Build Prep

- Public GitHub source repository publishing script.
- Reproducible-ish Windows CI build (PyInstaller on Windows + Inno Setup installer).
- Draft prerelease creation with installer and SHA-256 checksum.
- Optional read-only update notifications linking to official GitHub Releases, without auto-installing software.
- Expanded security documentation and safe `.gitignore` for encrypted vault data.
- Existing encrypted storage engine and its on-disk format remain unchanged from v0.9.

**Experimental:** No independent security audit, no signing, no Windows Hello key-unlock, and no multi-process vault lock. Do not store the only copy of important files.
