## Docker Hub + GHCR publication and installation fixes (2026-10-22)

- Publish both `dosenthegit/packmount` and `ghcr.io/dosenthegit/packmount` from one GitHub Actions build.
- Require `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` GitHub repository secrets.
- Fix Samba environment variable mismatch: `PACKMOUNT_SMB_USER` / `PACKMOUNT_SMB_PASSWORD` now work as documented; old `PACKMOUNT_SAMBA_*` variables still work as fallbacks.
- Replace placeholder clone URL with the PackMount repository URL and document reliable bind-mount propagation and binary-image deployment.
- Note that runtime SMB, NFS and FUSE integration still requires validation on a real Linux Docker host.

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

### GitHub publishing setup (2026-10-22)
- Added an Actions workflow to publish amd64 container images to GitHub Container Registry on pushes and manual dispatch.
- Added `latest`, git-SHA and semantic-version container tags.
- Added `docker-compose.ghcr.yml` and documented how to pull/run without a local build.
