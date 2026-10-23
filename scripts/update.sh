#!/usr/bin/env bash
# Update a PackMount deployment that uses docker-compose.ghcr.yml (Docker Hub or GHCR).
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE="docker-compose.ghcr.yml"
BACKUP_DIR="${PACKMOUNT_BACKUP_DIR:-$ROOT/../packmount-backups}"

if [[ ! -f .env ]]; then echo 'ERROR: .env is missing.' >&2; exit 1; fi
if [[ ! -f "$COMPOSE" ]]; then echo "ERROR: $COMPOSE missing." >&2; exit 1; fi
if [[ ! -d config ]]; then echo 'ERROR: config/ is missing; refusing to create an empty configuration.' >&2; exit 1; fi
if ! command -v docker >/dev/null; then echo 'ERROR: docker is unavailable.' >&2; exit 1; fi
if ! docker compose version >/dev/null 2>&1; then echo 'ERROR: Docker Compose v2 required.' >&2; exit 1; fi
if [[ "$(docker compose -f "$COMPOSE" config --images | wc -l)" -ne 1 ]]; then echo 'ERROR: expected a single image.' >&2; exit 1; fi

IMAGE="$(docker compose -f "$COMPOSE" config --images | head -n 1)"
printf 'Updating PackMount from %s\n' "$IMAGE"
# Pull first. Failure leaves the old container running.
docker compose -f "$COMPOSE" pull

# Stop cleanly before snapshotting SQLite, session keys and encrypted credentials.
docker compose -f "$COMPOSE" down
mkdir -p "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/config-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$BACKUP" -C "$ROOT" config
chmod 600 "$BACKUP"
echo "Backup saved: $BACKUP"

if ! docker compose -f "$COMPOSE" up -d --no-build; then
  echo 'ERROR: container start failed; backup retained. Manual rollback may be required.' >&2
  exit 1
fi

HEALTH_OK=0
for i in $(seq 1 30); do
  if docker compose -f "$COMPOSE" exec -T packmount /usr/local/bin/healthcheck.sh >/dev/null 2>&1; then
    HEALTH_OK=1
    break
  fi
  sleep 2
done

if [[ "$HEALTH_OK" -ne 1 ]]; then
  echo 'ERROR: container failed health check; no automatic rollback attempted.' >&2
  docker compose -f "$COMPOSE" ps >&2 || true
  docker compose -f "$COMPOSE" logs --tail=60 packmount >&2 || true
  exit 1
fi

echo 'PackMount update completed. Verify FUSE mounts, Samba and NFS from clients.'
docker compose -f "$COMPOSE" ps
