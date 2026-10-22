#!/usr/bin/env bash
# PackMount host prerequisites installer
# Supports Ubuntu/Debian, Fedora/RHEL-family, and Alpine Linux.
set -Eeuo pipefail

log() { printf '\n[PackMount] %s\n' "$*"; }
warn() { printf '[WARNING] %s\n' "$*" >&2; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

[[ $(id -u) -eq 0 ]] || die 'Run with sudo or as root.'
[[ $(uname -s) == Linux ]] || die 'Only Linux hosts are supported.'
[[ -f /etc/os-release ]] || die 'Cannot identify distribution.'
# shellcheck disable=SC1091
. /etc/os-release
DISTRO="${ID:-unknown}"
LIKE=" ${ID_LIKE:-} "

has() { command -v "$1" >/dev/null 2>&1; }

# Port diagnostics are advisory. No service is stopped unless explicitly requested.
CHECK_ONLY=0
DISABLE_RPC=0
for arg in "$@"; do
  case "$arg" in
    --check-only) CHECK_ONLY=1 ;;
    --disable-host-rpcbind) DISABLE_RPC=1 ;;
    -h|--help)
      cat <<'HELP'
Usage: sudo ./install-packmount-deps.sh [--check-only | --disable-host-rpcbind]
  --check-only             Inspect host, mounts and ports; install nothing.
  --disable-host-rpcbind   Explicitly stop/disable systemd rpcbind service/socket
                           ONLY if no host NFS mounts or NFS server are detected.
The option to disable rpcbind is explicit and requires manual confirmation.
Do not disable rpcbind when NFSv3 or other RPC services rely on it.
HELP
      exit 0 ;;
    *) die "Unknown option: $arg" ;;
  esac
done
(( CHECK_ONLY == 0 || DISABLE_RPC == 0 )) || die 'Choose one action at a time.'

host_nfs_in_use() {
  if has findmnt && [[ -n "$(findmnt -rn -t nfs,nfs4 -o TARGET 2>/dev/null || true)" ]]; then
    warn 'NFS mounts detected on host:'
    findmnt -rn -t nfs,nfs4 -o TARGET,SOURCE,FSTYPE >&2 || true
    return 0
  fi
  if has systemctl; then
    local unit
    for unit in nfs-server.service nfs-kernel-server.service; do
      if systemctl is-active --quiet "$unit" 2>/dev/null; then
        warn "Active host NFS server: $unit"
        return 0
      fi
    done
  fi
  # Conservative: even an inactive export configuration requires manual review.
  if [[ -s /etc/exports ]] && grep -Eq '^[[:space:]]*[^#[:space:]]' /etc/exports; then
    warn 'Non-comment NFS exports found in /etc/exports.'
    return 0
  fi
  return 1
}

port_report() {
  log 'Checking TCP/UDP host ports 111 (RPC) and 2049 (NFS).'
  if ! has ss; then
    warn 'ss is unavailable; cannot check listening sockets.'
    return
  fi
  local matches
  matches=$(ss -H -lntup 2>/dev/null | awk '$5 ~ /:(111|2049)$/ {print}' || true)
  if [[ -n "$matches" ]]; then
    printf '%s\n' "$matches"
    warn 'Port conflict possible if Compose publishes the same host IP/port/protocol.'
  else
    log 'No listeners on 111 or 2049 detected (TCP/UDP).'
  fi
}

if (( CHECK_ONLY )); then
  log 'Read-only diagnostics requested; no packages or services will be changed.'
  host_nfs_in_use || log 'No host NFS mounts/server/exports detected by basic checks.'
  port_report
  [[ -c /dev/fuse ]] && log '/dev/fuse exists.' || warn '/dev/fuse not found.'
  if has docker; then docker --version; docker compose version 2>/dev/null || true; fi
  exit 0
fi

# This script intentionally does not install a host NFS/Samba server, expose ports,
# change Docker daemon settings, create /dev/fuse manually, or edit mount propagation.
case "$DISTRO" in
  ubuntu|debian) FAMILY=apt ;;
  alpine) FAMILY=apk ;;
  fedora|rhel|centos|rocky|almalinux|ol) FAMILY=dnf ;;
  *)
    if [[ $LIKE == *debian* ]]; then FAMILY=apt
    elif [[ $LIKE == *rhel* || $LIKE == *fedora* ]]; then FAMILY=dnf
    else die "Unsupported distribution: $DISTRO"; fi ;;
esac

log "Detected ${PRETTY_NAME:-$DISTRO} ($FAMILY)"
case "$FAMILY" in
  apt)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y ca-certificates curl gnupg git fuse3 cifs-utils nfs-common kmod util-linux
    if ! has docker || ! docker compose version >/dev/null 2>&1; then
      log 'Installing Docker Engine and Compose v2 from official Docker apt repository.'
      . /etc/os-release
      case "$ID" in
        ubuntu|debian) REPO_OS="$ID" ;;
        *) die 'Automatic Docker repository configuration requires Ubuntu or Debian. Install Docker Engine and Compose v2 manually.' ;;
      esac
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL "https://download.docker.com/linux/${REPO_OS}/gpg" -o /etc/apt/keyrings/docker.asc
      chmod a+r /etc/apt/keyrings/docker.asc
      ARCH=$(dpkg --print-architecture)
      CODENAME=${VERSION_CODENAME:-}
      [[ -n "$CODENAME" ]] || die 'VERSION_CODENAME is missing.'
      printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' "$ARCH" "$REPO_OS" "$CODENAME" > /etc/apt/sources.list.d/docker.list
      apt-get update
      apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    ;;
  dnf)
    DNF=dnf
    has dnf || DNF=yum
    "$DNF" install -y ca-certificates curl git fuse3 cifs-utils nfs-utils kmod util-linux
    if ! has docker || ! docker compose version >/dev/null 2>&1; then
      if [[ $DISTRO == fedora ]]; then
        "$DNF" install -y dnf-plugins-core
        "$DNF" config-manager addrepo --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo 2>/dev/null || "$DNF" config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
      else
        "$DNF" install -y dnf-plugins-core
        "$DNF" config-manager addrepo --from-repofile=https://download.docker.com/linux/centos/docker-ce.repo 2>/dev/null || "$DNF" config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
      fi
      "$DNF" install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    ;;
  apk)
    apk update
    apk add docker docker-cli-compose fuse3 cifs-utils nfs-utils kmod util-linux git curl ca-certificates
    ;;
esac

# nfs-common/nfs-utils may start rpcbind automatically. Never shut it down silently.
log 'Enabling Docker service.' 
if has systemctl; then
  systemctl enable --now docker
elif has rc-update && has rc-service; then
  rc-update add docker default
  rc-service docker start || warn 'Docker service did not start.'
else
  warn 'Service manager not recognized: start Docker manually.'
fi

if (( DISABLE_RPC )); then
  log 'Explicit request to disable host rpcbind.'
  if host_nfs_in_use; then
    die 'Refusing to stop rpcbind: host NFS is in use or configured. Inspect services manually.'
  fi
  if ! has systemctl; then
    die 'Automatic rpcbind management supported only on systemd hosts.'
  fi
  if [[ ! -t 0 ]]; then
    die 'Interactive confirmation required for --disable-host-rpcbind.'
  fi
  printf 'Disable host rpcbind socket/service? Type DISABLE to confirm: '
  read -r confirmation
  [[ "$confirmation" == DISABLE ]] || die 'Cancelled; rpcbind unchanged.'
  if systemctl is-active --quiet rpcbind.socket || systemctl is-active --quiet rpcbind.service; then
    log 'Stopping and disabling rpcbind.socket and rpcbind.service.'
  fi
  systemctl disable --now rpcbind.socket rpcbind.service || die 'Could not disable rpcbind; inspect systemctl status.'
fi

port_report
log 'Checking kernel functionality.' 
for module in fuse cifs nfs nfsd; do
  if has modprobe && modprobe "$module" 2>/dev/null; then
    printf '  %-5s OK\n' "$module"
  elif [[ -d "/sys/module/$module" ]]; then
    printf '  %-5s OK (built in/already loaded)\n' "$module"
  else
    warn "Kernel module '$module' unavailable. Corresponding PackMount features may not work."
  fi
done

if [[ -c /dev/fuse ]]; then
  log '/dev/fuse exists (character device).'
else
  warn '/dev/fuse is absent. PackMount cannot use rar2fs/FUSE until the host exposes it.'
  warn 'In an LXC container or restricted VM, configure FUSE support on the outer host first.'
fi

log 'Installed versions / diagnostics:'
if has docker; then
  docker --version || true
  docker compose version || warn 'Docker Compose v2 unavailable.'
  docker info --format 'Docker daemon: {{.ServerVersion}}' || warn 'Docker daemon not responding.'
else
  warn 'Docker command not found.'
fi
has mount.cifs && mount.cifs -V || true
has mount.nfs && printf 'NFS mount helper present\n' || true
has findmnt && findmnt -T / -o TARGET,PROPAGATION || true

cat <<'NEXT'

PackMount host dependency setup finished.
Next steps (NOT performed automatically):
  1. Confirm /dev/fuse exists: ls -l /dev/fuse
  2. Review the compose file, .env secrets, ports and privileges.
  3. Configure persistent rshared propagation on PackMount's data and mounts directories.
  4. Review the host port diagnostics for 111/2049 before running Compose.
  5. Deploy the PackMount image using its Docker Compose file.

Note: Host kernel NFS server and port forwarding can require further configuration.
Use --check-only for safe diagnostics. On an unused NFS host you can explicitly
run --disable-host-rpcbind; it will refuse if NFS use/configuration is detected.
Never expose the privileged PackMount UI or file sharing services to untrusted networks.
NEXT
