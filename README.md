# PackMount 1.3.0

PackMount is a Linux/Docker web UI for presenting RAR archive contents with `rar2fs`, mounting local/SMB/NFS source folders, and exporting mounted views through Samba or NFS. Archives are accessed on demand, **not permanently extracted**.

![PackMount UI](docs/mockup.png)

## Prerequisites and security

- **Linux Docker Engine** with Docker Compose v2; `/dev/fuse` must exist. Host kernel must support FUSE, CIFS, NFS client and NFS server as applicable.
- **Privileged container.** Mounting and kernel NFS require powerful host privileges. Run only on a dedicated/trusted Docker host. Avoid exposing the web UI to the internet.
- **Ports:** TCP 8080 for UI; TCP 139/445 for SMB; TCP/UDP 111, 2049, 20048 for NFSv3-style services. Expose only the services you need, and firewall access to trusted subnets. Note that NFS kernel services and port publishing may depend on host configuration.
- Compose uses Docker bridge networking. FUSE propagation and kernel NFS export accessibility can differ by host; **NFS exports are not guaranteed to work behind Docker bridge/NAT**. For NFS production deployment, verify the host kernel, network topology and firewall before use.

## Installation

```bash
git clone <YOUR-REPOSITORY-URL> packmount
cd packmount
cp .env.example .env
# Use distinct, strong values for PACKMOUNT_PASSWORD and PACKMOUNT_SMB_PASSWORD
nano .env
mkdir -p config data mounts
# Mount propagation is important for visibility of FUSE mounts on the host.
sudo mount --make-rshared "$(pwd)/mounts"
sudo mount --make-rshared "$(pwd)/data"
docker compose config
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8080/api/health
```

The `mount --make-rshared` command may fail if the directory is not a separate mount point. On many Linux distributions first make the directories bind mount points (`sudo mount --bind mounts mounts` and `sudo mount --bind data data`) then mark them rshared. These host mount settings need to persist after reboot; see [Operations guide](docs/OPERATIONS.md). If propagation is not set up, mounting may work **inside** the container while the host cannot see the mounted view.

Open `http://HOST_IP:8080` and sign in using `.env` values. Keep the host firewall restricted to trusted networks, ideally use a TLS reverse proxy and `PACKMOUNT_SECURE_COOKIE=true`.

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
