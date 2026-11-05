# Security status — VEIL experimental preview (v0.10.0-beta.1)

**Not independently security audited. Not recommended for production use, sensitive professional data, or as the only copy of personal files.**

## What is encrypted
- File bytes, file paths/filenames, folder structure, camera photo bytes, and activity records use an established cryptography library (AES-256-GCM, per-object HKDF keys, Argon2id password-derived master-key wrap).
- The randomly generated recovery key provides a *second way to decrypt the same master key*, not protection against disk failure. Store it offline.
- `.json` metadata, folder structure on disk, file count, object sizes, file modification times, ciphertext volume, and the existence of a vault may still be visible.

## Important known risks and unfinished mitigations
1. **No independent audit.** This includes password/recovery handling, cryptography usage, preview decoders, archive parsing, and third-party binaries.
2. **No cross-process vault lock/transaction journal.** Running VEIL on the same vault in multiple processes or backing up during writes can lose changes or create inconsistent backups. Do not open a vault in two processes.
3. **Plaintext export temporary files.** Explicit exporting writes an unencrypted staging file before final authentication/rename; interrupted exports may leave plaintext artifacts on the destination filesystem. This does not affect files stored at rest in the encrypted vault. Do not export confidential files onto untrusted/shared drives.
4. **No rollback protection.** An adversary with drive access can replace the encrypted manifest or vault with an older copy, delete files, or corrupt them.
5. **In-memory exposure.** Previews, videos, and password-derived keys exist in process memory while unlocked, which Windows paging and local malware can access. No secure RAM zeroization guarantee.
6. **No secure deletion.** Deleting from the recycle bin or removing source files cannot reliably erase SSD remnants or prior plaintext copies.
7. **Windows session auto-lock is best-effort.** It is not a guaranteed security boundary and needs actual Windows sleep/lock testing.
8. **Activity-camera evidence is not tamperproof.** Camera is off by default, and when enabled records one opt-in encrypted snapshot on launch, not an identity check. Privileged attackers can disable it or delete events.
9. **No hardware-bound Windows Hello unlocking yet.** A PIN/biometric prompt without protecting the encryption keys would be misleading.
10. **Unsigned preview installer.** Windows SmartScreen/Defender may warn. Check its SHA-256 and only obtain from the owner's GitHub repository. No automatic executable update or execution is implemented.
11. **Dependency and media-decoder risk.** OpenCV/PyAV decode external files; unsupported or malicious media could exploit bugs in those native libraries. Keep them updated.
12. **No external security verification or code signing.** Do not represent this beta as a secure audited release.

## Production release gate
- External cryptographic implementation review and threat model.
- Introduce robust multi-process locking, journaled metadata writes, and crash/recovery test suite.
- Resolve plaintext staging risk and validate secure export behavior.
- Audit video/ZIP parsers and encrypted capture logging.
- Validate recovery, backup and restore, lock/sleep, and multiple Windows devices.
- Establish code signing and a verifiable, rollback-resistant update path.
- Use a security issue/disclosure process and dependency monitoring.

## Disclosure

Until a contact address is published, use GitHub Issues for non-sensitive bug reports. **Do not submit exploit details publicly**; contact the repository owner privately for security findings.
