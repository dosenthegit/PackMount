# PackMount 1.3.0

PackMount is a Linux/Docker web UI for presenting RAR archive contents with `rar2fs`, mounting local/SMB/NFS source folders, and exporting mounted views through Samba or NFS. Archives are accessed on demand, **not permanently extracted**.

![PackMount UI](docs/mockup.png)

## Prerequisites and security

- **Linux Docker Engine** with Docker Compose v2; `/dev/fuse` must exist. Host kernel must support FUSE, CIFS, NFS client and NFS server as applicable.
- **Privileged container.** Mounting and kernel NFS require powerful host privileges. Run only on a dedicated/trusted Docker host. Avoid exposing the web UI to the internet.
- **Ports:** TCP 8080 for UI; TCP 139/445 for SMB; TCP/UDP 111, 2049, 20048 for NFSv3-style services. Expose only the services you need, and firewall access to trusted subnets. Note that NFS kernel services and port publishing may depend on host configuration.
- Compose uses Docker bridge networking. FUSE propagation and kernel NFS export accessibility can differ by host; **NFS exports are not guaranteed to work behind Docker bridge/NAT**. For NFS production deployment, verify the host kernel, network topology and firewall before use.

## Installation (build from GitHub source)

Run on a **Linux Docker host** (not Docker Desktop). Install Git, Docker Engine and Docker Compose v2 first. Check that `/dev/fuse` exists. The host must support the applicable CIFS/NFS/FUSE kernel features.

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

After cloning, preparing `.env`, making the directories and arranging mount propagation as above, set `PACKMOUNT_IMAGE=dosenthegit/packmount:latest` in `.env` (or use `ghcr.io/dosenthegit/packmount:latest`). Then:

```bash
docker compose -f docker-compose.ghcr.yml config
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d --no-build
docker compose -f docker-compose.ghcr.yml ps
curl -fsS http://127.0.0.1:8080/api/health
```

Docker Hub images are public only if their repository is public; GHCR images also require **public package visibility** or a login with read permission. Prefer a numbered release tag for reproducibility, rather than `latest`.

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

## GitHub Actions: publish to Docker Hub and GHCR

Workflow path: `.github/workflows/docker-publish.yml`. It builds `linux/amd64` once and pushes the generated tags to **both** registries:

- Docker Hub: `dosenthegit/packmount:latest`
- GitHub Container Registry: `ghcr.io/dosenthegit/packmount:latest`

### Required one-time setup

1. On [Docker Hub](https://hub.docker.com/), sign into the `dosenthegit` account and create a repository named `packmount`, or ensure that account can publish to `dosenthegit/packmount`. Create a Docker Hub **access token with write permissions**; do not use your password.
2. On GitHub, open `https://github.com/dosenthegit/PackMount/settings/secrets/actions` and create two **Repository secrets**: `DOCKERHUB_USERNAME` = `dosenthegit` and `DOCKERHUB_TOKEN` = your Docker Hub access token.
3. Under GitHub **Settings → Actions → General → Workflow permissions**, ensure the workflow can publish packages; `.github/workflows/docker-publish.yml` explicitly requests `contents: read` and `packages: write`. The GHCR login uses the automatic `GITHUB_TOKEN`; no extra GHCR personal token is required.
4. Commit/push all files, including the hidden `.github` folder, to `main`. **Push to main triggers publication automatically**, or use **Actions → Build and publish Docker images → Run workflow → main**.
5. In Docker Hub and GHCR, configure public visibility if you need anonymous pulls. Check that the workflow has succeeded and both published images appear before deploying.

### Image tags

- Push to `main`: `latest` plus an immutable-looking `sha-...` tag (a tag can still be overwritten by privileged users).
- Push the Git tag `v1.3.0`: publishes `1.3.0`, `1.3` and an SHA tag to **both** registries. The `latest` tag follows `main`, not version-tag pushes.
- Runner: `ubuntu-24.04`, platform: `linux/amd64` (not ARM64).

To create a version release after committing the appropriate source code:

```bash
git tag v1.3.0
git push origin v1.3.0
```

Do not recreate a tag if it already exists. Keep the `VERSION` file and Git tags in sync.

### Run the published image

Keep your existing `.env`, `config/`, `data/` and `mounts/` directories, especially `config/fernet.key`. Add **one** `PACKMOUNT_IMAGE` line to `.env`:

```dotenv
PACKMOUNT_IMAGE=dosenthegit/packmount:latest
# Alternative: ghcr.io/dosenthegit/packmount:latest
```

The supplied `docker-compose.ghcr.yml` accepts **either Docker Hub or GHCR** despite its historical filename. Run the same setup for mount propagation described under Installation, then:

```bash
docker compose -f docker-compose.ghcr.yml config
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d --no-build
docker compose -f docker-compose.ghcr.yml ps
```

To upgrade an existing deployment, see [Operations guide](docs/OPERATIONS.md). `docker compose up -d --build` is only for the source-build Compose file; it is not needed for a prebuilt image.
