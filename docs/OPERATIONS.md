# Operations, upgrade and recovery

## Before upgrading

Stop mounts and SMB/NFS clients gracefully; schedule a maintenance window. Keep a copy of the previous application checkout/image.

```bash
docker compose down
mkdir -p ../packmount-backups
tar -czf "../packmount-backups/config-$(date +%Y%m%d-%H%M%S).tar.gz" -C . config
# Ensure backup includes config/fernet.key, config/session.secret, database and any WAL files.
```

**Never delete or regenerate `config/fernet.key`** when upgrading: encrypted passwords depend on it. Protect backups since they contain authentication data.

## Deploy

Replace repository source files but keep `config/`, `data/`, `mounts/` and existing `.env` unchanged. Compare `.env` with the new `.env.example` and set strong passwords. Review changed Samba user mapping and NFS root-squash permissions.

```bash
mkdir -p config data mounts
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 packmount
curl -fsS http://127.0.0.1:8080/api/health
```

`docker compose restart` alone **does not** rebuild the image. `up -d --build` does. After deployment verify: login, local sources, remote SMB/NFS sources, the rar2fs view, SMB client access and any NFS clients.

## Host bind mount propagation

The Docker bind mounts request `rshared` propagation so FUSE mounts can be visible to the host. Host paths must support shared propagation. Example (run from project directory on Linux):

```bash
sudo mount --bind mounts mounts
sudo mount --make-rshared mounts
sudo mount --bind data data
sudo mount --make-rshared data
findmnt -o TARGET,PROPAGATION | grep -E 'mounts|data'
```

This is a one-time setup only until reboot: make it persistent using properly reviewed systemd mount units or host fstab entries. Never blindly change mount propagation on production storage without reviewing the mount tree.

## Diagnostics

```bash
ls -l /dev/fuse
docker compose ps
docker compose exec packmount command -v rar2fs
docker compose exec packmount mountpoint /mnt/packmount/YOUR_MOUNT
docker compose exec packmount testparm -s
docker compose exec packmount exportfs -v
ss -lntup | grep -E ':(8080|445|139|111|2049|20048)\b'
```

If port 445 or 2049 is busy, remove unneeded mappings or stop conflicting host services; avoid publishing the API or fileshares publicly. SMB access may change because `force user` is now the configured Samba account rather than root; change ownership/ACLs on the underlying mounted data only if appropriate. NFS defaults to `root_squash`, which may require correct UID/GID permissions. Re-exported FUSE views can be sensitive to permissions and kernel behavior.

## Rollback

```bash
docker compose down
# Restore your previous source checkout, Compose files and .env.
# Restore config backup only if the upgrade modified data and you need full rollback.
tar -xzf ../packmount-backups/config-YOUR-TIMESTAMP.tar.gz -C .
docker compose up -d --build
```

Do not extract a backup over a running SQLite database. Restoring an older DB discards any state changes made after backup.

## Deploy and update from Docker Hub or GHCR

Use `docker-compose.ghcr.yml` with `PACKMOUNT_IMAGE=dosenthegit/packmount:latest` (or `ghcr.io/dosenthegit/packmount:latest`) in `.env`. Preserve the entire `/config` host bind directory, particularly `fernet.key`. Backup first as described above; then:

```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d --no-build
docker compose -f docker-compose.ghcr.yml ps
```

The `rshared` host mount setup must be in place before startup and persist after reboot. Do not use `docker compose up -d --build` with the published-image file. Check SMB and NFS access after upgrading.
