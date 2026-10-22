# Changelog

## 1.3.0 - 2026-10-22

- Added previously missing `.env.example`, `.gitignore` and `.dockerignore`.
- Added Docker healthcheck, bounded container JSON logs and simplified bridge override.
- Reject insecure default web and Samba passwords on startup.
- Hardened encrypted secret key handling: do not silently overwrite an invalid Fernet key.
- Limit writable mount destinations to immediate children of the mount root and shares to registered mounts.
- Validate remote source paths and share client strings, reducing configuration injection risk.
- Prevent plaintext SMB credentials from remaining in runtime credential files after a successful or failed mount command.
- Switch default remote NFS mount behavior from `soft` to `hard` and export roots from `no_root_squash` to `root_squash`.
- Avoid forcing Samba share access as root, use the configured Samba account.
- Added basic same-origin checks for authenticated state-changing requests and a health endpoint.
- Added new installation, upgrade, rollback and troubleshooting documentation.

### Potentially breaking security changes

- Previously insecure/default passwords must now be replaced.
- SMB and NFS access permissions can change and should be retested.
- Mount destination paths must be direct children of `/mnt/packmount`; existing nested records are not modified, but the UI will reject newly created nested paths.

### Verification

Python syntax and shell syntax validated. Docker image build and kernel mount integration require a real Linux Docker host.
