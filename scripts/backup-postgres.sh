#!/usr/bin/env bash
#
# backup-postgres.sh - Stop postgres, tar data dir, start again.
#
# Cron (Sunday 03:00):
#   0 3 * * 0 /path/to/deer-flow/scripts/backup-postgres.sh >> /data/postgres-backups/backup.log 2>&1
#
# Env (optional):
#   POSTGRES_CONTAINER          default deer-flow-postgres
#   POSTGRES_DATA_DIR           default /data/postgres
#   POSTGRES_BACKUP_DIR         default /data/postgres-backups
#   POSTGRES_BACKUP_KEEP_WEEKS  default 8
#

set -euo pipefail

CONTAINER="${POSTGRES_CONTAINER:-deer-flow-postgres}"
DATA_DIR="${POSTGRES_DATA_DIR:-/data/postgres}"
BACKUP_DIR="${POSTGRES_BACKUP_DIR:-/data/postgres-backups}"
KEEP_WEEKS="${POSTGRES_BACKUP_KEEP_WEEKS:-8}"
OUT="${BACKUP_DIR}/postgres_$(date +%Y%m%d).tar.gz"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

mkdir -p "$BACKUP_DIR"
[ -d "$DATA_DIR" ] || { log "ERROR: data dir not found: $DATA_DIR"; exit 1; }

# Always try to bring postgres back up, even if tar fails.
trap 'docker start "$CONTAINER" >/dev/null || true' EXIT

log "Stopping $CONTAINER"
docker stop "$CONTAINER" >/dev/null

log "Archiving $DATA_DIR -> $OUT"
tar -czf "$OUT" -C "$(dirname "$DATA_DIR")" "$(basename "$DATA_DIR")"

log "Starting $CONTAINER"
docker start "$CONTAINER" >/dev/null
trap - EXIT

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'postgres_*.tar.gz' \
  -mtime "+$((KEEP_WEEKS * 7))" -delete

log "Done: $OUT ($(du -h "$OUT" | awk '{print $1}'))"
