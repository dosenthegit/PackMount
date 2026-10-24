# PackMount 1.3.0

PackMount is a Linux/Docker web UI for presenting RAR archive contents with `rar2fs`, mounting local/SMB/NFS source folders, and exporting mounted views through Samba or NFS. Archives are accessed on demand, **not permanently extracted**.

![PackMount UI](docs/mockup.png)

## Prerequisites and security

- **Linux Docker Engine** with Docker Compose v2; `/dev/fuse` must exist. Host kernel must support FUSE, CIFS, NFS client and NFS server as applicable.
- **Privileged container.** Mounting and kernel NFS require powerful host privileges. Run only on a dedicated/trusted Docker host. Avoid exposing the web UI to the internet.
- **Ports:** TCP 8080 for UI; TCP 139/445 for SMB; TCP/UDP 111, 2049, 20048 for NFSv3-style services. Expose only the services you need, and firewall access to trusted subnets. Note that NFS kernel services and port publishing may depend on host configuration.
- Compose uses Docker bridge networking. FUSE propagation and kernel NFS export accessibility can differ by host; **NFS exports are not guaranteed to work behind Docker bridge/NAT**. For NFS production deployment, verify the host kernel, network topology and firewall before use.

## Host dependencies (optional preparation step)

The host setup helper [`scripts/install-dependencies.sh`](scripts/install-dependencies.sh) is **still supported**. It is intended for a **Linux host**, not for execution inside the PackMount Docker container. Run it on a fresh server or when checking missing prerequisites; it is **not needed for normal image updates** if the host is already configured. It supports Ubuntu/Debian, Fedora/RHEL-family and Alpine (package availability varies).

After cloning this repository, inspect the available options and run read-only checks:

```bash
bash scripts/install-dependencies.sh --help
sudo bash scripts/install-dependencies.sh --check-only
```

On a new host, install required utilities and Docker Engine/Compose v2 if absent:

```bash
sudo bash scripts/install-dependencies.sh
```

The script installs host-side Git, FUSE/CIFS/NFS client tools and Docker components when needed, checks ports 111/2049 and `/dev/fuse`, and attempts to enable Docker. It does **not** deploy PackMount, create persistent `rshared` mounts, configure your `.env`, or guarantee that the host supports all required kernel facilities. Review its output before starting Compose. It may change system packages/services; use `--check-only` first on existing systems.

If port 111 conflicts with the host's `rpcbind`, investigate before changing anything. An **advanced, optional** `--disable-host-rpcbind` mode requires interactive confirmation and refuses when it detects existing NFS use. Never use this mode on hosts that depend on NFS/RPC services.

For installation, continue with the source-build steps below or the published-image section. For an existing installation, see **Updating PackMount**; the dependency helper and [`scripts/update.sh`](scripts/update.sh) serve different purposes.

## Installation (build from GitHub source)

Run on a **Linux Docker host** (not Docker Desktop). Install Git first, then optionally run the host dependency helper above to provision Docker Engine and Docker Compose v2. Check that `/dev/fuse` exists. The host must support the applicable CIFS/NFS/FUSE kernel features.

```bash
git clone https://github.com/dosenthegit/PackMount.git packmount
cd packmount
cp .env.example .env
nano .env
# Set distinct, strong PACKMOUNT_PASSWORD and PACKMOUNT_SMB_PASSWORD.
mkdir -p config data mounts

# rshared propagation: make each path a mountpoint before setting propagation.
# Only run this after reviewing mounts on the host.
sudo mount --bind "$(pwd)/mounts" "$(pwd)/mounts"
sudo mount --make-rshared "$(pwd)/mounts"
sudo mount --bind "$(pwd)/data" "$(pwd)/data"
sudo mount --make-rshared "$(pwd)/data"
findmnt -T "$(pwd)/mounts" -o TARGET,PROPAGATION
findmnt -T "$(pwd)/data" -o TARGET,PROPAGATION

docker compose config
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8080/api/health
```

For mount propagation, **these bind mounts are not persistent across reboot**. Arrange persistent mount points/propagation as described in [Operations guide](docs/OPERATIONS.md) before relying on automatic restart. Avoid blindly adding duplicate bind mounts if these paths are already dedicated shared mounts. The container needs the host's `/dev/fuse`, and ports 8080/139/445/111/2049/20048 must not conflict with other services. NFS exports may not work through Docker bridge networking on every host. Restrict the management UI and file-sharing ports with the host firewall.

Open `http://HOST_IP:8080` and sign in using the credentials set in `.env`. If behind an HTTPS reverse proxy, set `PACKMOUNT_SECURE_COOKIE=true` and test the login flow.

### Install using a published image instead of building locally

After cloning, preparing `.env`, making the directories and arranging mount propagation as above, set `PACKMOUNT_IMAGE=d0sn/packmount:latest` in `.env` (or use `ghcr.io/dosenthegit/packmount:latest`). Then:

```bash
docker compose -f docker-compose.ghcr.yml config
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d --no-build
docker compose -f docker-compose.ghcr.yml ps
curl -fsS http://127.0.0.1:8080/api/health
```

Docker Hub images are public only if their repository is public; GHCR images also require **public package visibility** or a login with read permission. Prefer a numbered release tag for reproducibility, rather than `latest`.

## Updating PackMount (published Docker images)

For a production installation, use a **specific version tag** (`d0sn/packmount:1.3.0`) when available rather than `latest`. For an installation tracking `latest`, the following pulls the newest image. Updating always restarts the container and may interrupt SMB/NFS clients and active FUSE mounts; schedule maintenance accordingly.

From your existing checkout, with `.env` containing `PACKMOUNT_IMAGE=d0sn/packmount:latest` (or the GHCR equivalent):

```bash
./scripts/update.sh
```

The script downloads the image *before* stopping the existing container, stops it, saves a timestamped `config/` backup in `../packmount-backups`, recreates the container, and checks the API health endpoint. It does **not** automatically roll back failures and cannot verify actual FUSE/SMB/NFS operation. Back up or snapshot `data/` independently; it is not included. The script does not update the source checkout or itself, so run `git pull --ff-only` separately if new deployment scripts or Compose changes have been published. Never run `git pull` over uncommitted local changes.

For a **source-built** installation (`docker-compose.yml`), update project files with Git and run `docker compose up -d --build` after a stopped-container `config/` backup. Do not use `scripts/update.sh` for source builds.

Fully automatic unattended upgrades are **not enabled by default**, because forced restarts may disrupt active mounts or clients. A controlled maintenance schedule is safer. See [Operations](docs/OPERATIONS.md) for precautions and recovery steps.

## Add an archive source

In the UI select **Add source**. For local folders use a path under `/data`, e.g. `/data/archive`; for SMB use `//nas/share`; for NFS use `192.168.1.10:/exports/archive`. Set **Mount to** to an immediate subdirectory of `/mnt/packmount`, e.g. `/mnt/packmount/archive`. Choose whether to start automatically on application startup and whether the source should be read only. Click **Mount**. Local host data under `./data` appears as `/data` inside the container.

To re-share, select a registered mount in the UI, then create a share. When guest access is disabled, Samba authenticates with `PACKMOUNT_SMB_USER` and `PACKMOUNT_SMB_PASSWORD`. NFS `Allowed clients` accepts client IPs, CIDRs and DNS names; use a restrictive subnet, e.g. `192.168.1.0/24`, rather than `*`. NFS exports use `root_squash` by default.

SMB client example: `\\HOST_IP\archive` on Windows, or `smb://HOST_IP/archive` on Linux/macOS (substitute the configured share name).

NFS clients use the path exposed by the host's NFS server; test using `showmount -e HOST_IP` before attempting `sudo mount -t nfs HOST_IP:/mnt/packmount/archive /mnt/test`. NFS is host-dependent and may require a different network deployment.

## Configuration

| Environment variable | Description |
| --- | --- |
| `PACKMOUNT_USER` | Web username (default `admin`) |
| `PACKMOUNT_PASSWORD` | **Required unique** web password |
| `PACKMOUNT_SMB_USER` | Samba user (default `packmount`) |
| `PACKMOUNT_SMB_PASSWORD` | **Required unique** Samba password |
| `PACKMOUNT_SMB_WORKGROUP` | Samba workgroup (default `WORKGROUP`) |
| `PACKMOUNT_SECURE_COOKIE` | Set `true` behind HTTPS proxy |
| `PACKMOUNT_SESSION_MAX_AGE` | Web cookie max age in seconds |
| `PACKMOUNT_ENABLE_SAMBA` | Start Samba daemons (`true` by default) |
| `PACKMOUNT_ENABLE_NFSD` | Start NFS daemons (`true` by default) |
| `PACKMOUNT_NFSD_THREADS` | Kernel NFS worker count (default 8) |
| `PACKMOUNT_MOUNTD_PORT` | mountd port (default 20048) |
| `PACKMOUNT_ALLOWED_LOCAL_ROOTS` | Allowed local folder roots (default `/data,/mnt,/media`) |

You can disable unneeded services in `.env`, **and remove their published ports** in Compose. Database and secrets live in `/config`.

## Upgrade from 1.2.3 or earlier

**Back up the entire `config` directory**, including SQLite WAL/SHM files and `fernet.key`, after stopping the container. Preserve the same Compose bind mounts. Then deploy this version and rebuild. See [Operations guide](docs/OPERATIONS.md) for commands and rollback. The database schema stays compatible. Existing shares using root privileges may behave differently because newly generated Samba configurations no longer use `force user = root`, and NFS now defaults to `root_squash`. Review filesystem ownership and client access after upgrading.

## Troubleshooting

```bash
docker compose ps
docker compose logs --tail=200 packmount
docker compose exec packmount testparm -s
docker compose exec packmount exportfs -v
docker compose exec packmount mount
```

See [Operations guide](docs/OPERATIONS.md) and [Changes](CHANGELOG.md).

## Important limitations

The container needs elevated privileges and cannot sandbox malicious paths as strongly as an unprivileged service. Local source and export configuration should only be given to trusted admins. `rar2fs` doesn't manage encrypted archive passwords in the UI. Passwords for remote SMB mounts are encrypted in SQLite, but the Fernet key is **stored in `/config` on the same host**; secure and back up that directory. This release is statically validated but **not integration-tested with actual FUSE/NFS/SMB mounts in Docker**.
