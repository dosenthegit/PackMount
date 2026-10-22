#!/usr/bin/env bash
set -Eeuo pipefail

mkdir -p /config /data /mnt/packmount /run/packmount/sources /run/packmount/credentials /var/log/samba /etc/exports.d
chmod 700 /run/packmount/credentials || true

if ! grep -q '^user_allow_other' /etc/fuse.conf 2>/dev/null; then
  echo 'user_allow_other' >> /etc/fuse.conf || true
fi

SAMBA_USER="${PACKMOUNT_SAMBA_USER:-packmount}"
SAMBA_PASSWORD="${PACKMOUNT_SAMBA_PASSWORD:?Set PACKMOUNT_SAMBA_PASSWORD in .env}"
if [ "$SAMBA_PASSWORD" = "packmount" ] || [ "$SAMBA_PASSWORD" = "REPLACE_WITH_DIFFERENT_STRONG_PASSWORD" ]; then
  echo "ERROR: Set a strong PACKMOUNT_SAMBA_PASSWORD" >&2; exit 1
fi
if ! id "$SAMBA_USER" >/dev/null 2>&1; then
  useradd -M -s /usr/sbin/nologin "$SAMBA_USER" || true
fi
printf '%s\n%s\n' "$SAMBA_PASSWORD" "$SAMBA_PASSWORD" | smbpasswd -a -s "$SAMBA_USER" >/dev/null 2>&1 || true
smbpasswd -e "$SAMBA_USER" >/dev/null 2>&1 || true

# Start base file sharing daemons. The API rewrites config files and reloads them.
if [ "${PACKMOUNT_ENABLE_SAMBA:-true}" = "true" ]; then
  smbd -D >/dev/null 2>&1 || true
  nmbd -D >/dev/null 2>&1 || true
fi

# Kernel NFS needs a privileged container and /proc/fs/nfsd.
if [ "${PACKMOUNT_ENABLE_NFSD:-true}" = "true" ]; then
mkdir -p /proc/fs/nfsd
if ! mountpoint -q /proc/fs/nfsd; then
  mount -t nfsd nfsd /proc/fs/nfsd >/dev/null 2>&1 || true
fi
rpcbind -w >/dev/null 2>&1 || rpcbind >/dev/null 2>&1 || true
rpc.statd --no-notify >/dev/null 2>&1 || true
rpc.nfsd "${PACKMOUNT_NFSD_THREADS:-8}" >/dev/null 2>&1 || true
rpc.mountd --manage-gids --port "${PACKMOUNT_MOUNTD_PORT:-20048}" >/dev/null 2>&1 || true
exportfs -ra >/dev/null 2>&1 || true
fi

exec "$@"
