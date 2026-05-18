# PackMount

PackMount is a Dockerized dark-glass web GUI for mounting archive folders through `rar2fs`, adding local/SMB/NFS sources, and re-sharing the mounted unpacked view over SMB or NFS.

The application includes:

- Login-protected web UI on `http://localhost:8080`
- Local folder source picker for container-visible host folders
- SMB source mounting through `mount.cifs`
- NFS source mounting through `mount.nfs`
- `rar2fs` FUSE mounts with `allow_other`
- Start, stop, delete, and automount handling
- SMB share management through Samba
- NFS export management through `exportfs`
- SQLite state database in `/config`
- Encrypted stored remote passwords using a Fernet key in `/config/fernet.key`

## Important host requirements

This project must run on a Linux Docker host for the real mount/share functionality. The GUI may start on Docker Desktop, but FUSE mounts, kernel NFS, CIFS/NFS client mounts, and mount propagation are Linux kernel features and usually will not work correctly on macOS/Windows Docker Desktop.

Required Linux host features:

- Docker Engine with Compose plugin
- `/dev/fuse` available on the host
- Container started with `privileged: true`, `/dev/fuse`, `SYS_ADMIN`, and AppArmor unconfined
- No existing host services bound to ports 139/445 for SMB or NFS ports if using host networking

## Quick start

```bash
cd packmount
cp .env.example .env
nano .env
mkdir -p config data mounts
```

Put a test folder with RAR files under `./data`, for example:

```bash
mkdir -p data/archive
# copy your .rar files into data/archive
```

Build and start:

```bash
docker compose up -d --build
```

Open:

```text
http://localhost:8080
```

Default login is configured in `.env`:

```text
PACKMOUNT_USER=admin
PACKMOUNT_PASSWORD=change-this-password
```

## Typical local mount flow

1. Open `http://localhost:8080`.
2. Log in.
3. In **Add source**, choose **Local folder**.
4. Use `/data/archive` or click **Browse**.
5. In **Mount settings**, use `/mnt/packmount/archive`.
6. Click **Mount**.
7. The mounted unpacked view is available inside the container at `/mnt/packmount/archive` and, with mount propagation, on the host under `./mounts/archive`.

## Typical SMB source flow

Use these values in the UI:

```text
Source type: SMB
Path / Server: //nas/archive
Username: your-smb-user
Password: your-smb-password
Mount to: /mnt/packmount/nas-archive
Mount name: NAS Archive
```

The backend first mounts `//nas/archive` into an internal runtime folder and then mounts the rar2fs view to the chosen mountpoint.

## Typical NFS source flow

Use these values in the UI:

```text
Source type: NFS
Path / Server: 192.168.1.50:/export/archive
Mount to: /mnt/packmount/nfs-archive
Mount name: NFS Archive
```

The backend first mounts the NFS export into an internal runtime folder and then mounts the rar2fs view to the chosen mountpoint.

## Sharing the mounted folder

After a mount is active, click the row in **Active mounts**. The share form will fill in the mounted path.

### SMB sharing

1. Select **SMB share**.
2. Set a share name, for example `Archive-share`.
3. Keep path as `/mnt/packmount/archive`.
4. Select `Read/write` or `Read-only`.
5. Enable guest access only if you want anonymous LAN access.
6. Click **Enable sharing**.

From another machine:

```text
smb://YOUR_DOCKER_HOST/Archive-share
```

If guest is disabled, use:

```text
Username: value of PACKMOUNT_SMB_USER
Password: value of PACKMOUNT_SMB_PASSWORD
```

### NFS sharing

1. Select **NFS share**.
2. Set a share name.
3. Keep path as the rar2fs mount path.
4. Use `Allowed clients` such as `192.168.1.0/24` or `*`.
5. Click **Enable sharing**.

From another Linux machine, a typical mount command is:

```bash
sudo mount -t nfs YOUR_DOCKER_HOST:/mnt/packmount/archive /mnt/test
```

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `PACKMOUNT_USER` | `admin` | Web login username |
| `PACKMOUNT_PASSWORD` | `admin` | Web login password. Change this. |
| `PACKMOUNT_SMB_USER` | `packmount` | Samba user created on container start |
| `PACKMOUNT_SMB_PASSWORD` | `packmount` | Samba password. Change this. |
| `PACKMOUNT_SMB_WORKGROUP` | `WORKGROUP` | Samba workgroup |
| `PACKMOUNT_ENABLE_SAMBA` | `true` | Start Samba daemon |
| `PACKMOUNT_ENABLE_NFSD` | `true` | Start kernel NFS services |
| `PACKMOUNT_DATA_PATH` | `./data` | Host folder mounted as `/data` in the container |
| `PACKMOUNT_ALLOWED_LOCAL_ROOTS` | `/data,/mnt,/media` | Roots visible to the local folder picker |

## Project layout

```text
packmount/
  Dockerfile
  docker-compose.yml
  docker-compose.bridge.yml
  .env.example
  backend/
    app.py
    requirements.txt
    static/
      index.html
      styles.css
      app.js
  scripts/
    entrypoint.sh
    healthcheck.sh
  docs/
    mockup.png
  config/
  data/
  mounts/
```

## Troubleshooting

Check the app logs:

```bash
docker logs -f packmount
```

Open a shell:

```bash
docker exec -it packmount bash
```

Check mounted filesystems:

```bash
mount | grep packmount
mountpoint /mnt/packmount/archive
```

Check Samba configuration:

```bash
docker exec -it packmount testparm -s
```

Check NFS exports:

```bash
docker exec -it packmount exportfs -v
```

If rar2fs fails with a FUSE error, verify that the container is started with `/dev/fuse`, `SYS_ADMIN`, `privileged: true`, and `apparmor:unconfined`.

## Security notes

- Change all passwords in `.env` before exposing the service to a network.
- Remote SMB passwords are encrypted in SQLite, but the encryption key is stored in `/config/fernet.key`. Protect the `config` folder.
- `Allow guest` means anonymous SMB access for that share.
- `Allowed clients` can restrict SMB/NFS access. Leave blank or use `*` only on trusted networks.
- This container performs privileged mount operations. Run it only on a host you control.

## Notes about rar2fs behavior

`rar2fs` exposes archive contents on demand. It does not permanently unpack archives. Encrypted RAR files require rar2fs-compatible password sidecar files, such as `.pwd` or `.rarconfig`; this GUI does not manage archive passwords.
