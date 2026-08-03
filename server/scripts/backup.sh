#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Daily backup: PostgreSQL dump + uploads/ mirror, 14-day retention.
#
#  DB DUMP
#  Compressed with gzip -9. Verified after writing by decompressing and
#  checking the dump ends with PostgreSQL's completion marker — a
#  truncated dump (disk full, OOM) otherwise looks like a valid file and
#  is only discovered to be useless during a restore.
#
#  UPLOADS
#  Uses rsync --link-dest against yesterday's snapshot. Unchanged .enc
#  files become hardlinks, not copies, so 14 daily snapshots of a 50 GB
#  uploads/ cost ~50 GB plus each day's new files — not 700 GB. The files
#  are AES-encrypted, so compressing them would waste CPU for nothing.
#
#  WARNING
#  These backups live on the SAME disk as the data they protect. That
#  covers accidental deletion, a bad migration, or a botched purge. It
#  does NOT cover disk failure or losing the VPS. Copy BACKUP_DIR to
#  off-site storage as well.
#
#  USAGE
#      bash server/scripts/backup.sh
#
#  CRON (daily 02:00)
#      0 2 * * * bash /path/to/server/scripts/backup.sh >> "$HOME/backup.log" 2>&1
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${ETS_BACKUP_DIR:-$HOME/ets-backups}"
RETENTION_DAYS=14
STAMP="$(date +%Y%m%d-%H%M%S)"
TODAY="$(date +%Y%m%d)"

[ -f "$SERVER_DIR/.env" ] || { echo "ERROR: $SERVER_DIR/.env not found"; exit 1; }
set -a; . "$SERVER_DIR/.env"; set +a
: "${DB_NAME:?DB_NAME missing from .env}"
: "${DB_USER:?DB_USER missing from .env}"

mkdir -p "$BACKUP_DIR/db" "$BACKUP_DIR/uploads"

echo "=== ETS backup  $(date "+%Y-%m-%dT%H:%M:%S%z") ==="

# ── 1. Database ────────────────────────────────────────────────────────
DUMP="$BACKUP_DIR/db/ets-$STAMP.sql.gz"
echo "[1/4] pg_dump -> $DUMP"
PGPASSWORD="${DB_PASSWORD:-}" pg_dump \
    -h "${DB_HOST:-localhost}" -p "${DB_PORT:-5432}" \
    -U "$DB_USER" -d "$DB_NAME" \
    | gzip -9 > "$DUMP"

# ── 2. Verify the dump ─────────────────────────────────────────────────
# A truncated dump is still a readable gzip file with plausible size, so
# check both that gzip is intact and that pg_dump actually finished.
echo "[2/4] verifying"
if ! gzip -t "$DUMP" 2>/dev/null; then
    echo "  FAILED: $DUMP is not a valid gzip archive"; rm -f "$DUMP"; exit 1
fi
if ! gzip -dc "$DUMP" | tail -5 | grep -q "PostgreSQL database dump complete"; then
    echo "  FAILED: dump is truncated (no completion marker)"; rm -f "$DUMP"; exit 1
fi
TABLES=$(gzip -dc "$DUMP" | grep -c "^CREATE TABLE" || true)
if [ "$TABLES" -lt 5 ]; then
    echo "  FAILED: only $TABLES tables in dump, expected at least 5"; rm -f "$DUMP"; exit 1
fi
echo "  OK: $(du -h "$DUMP" | cut -f1), $TABLES tables"

# ── 3. Uploads (hardlink-incremental) ──────────────────────────────────
UP_SRC="$SERVER_DIR/uploads"
UP_DST="$BACKUP_DIR/uploads/$TODAY"
if [ -d "$UP_SRC" ]; then
    PREV="$(find "$BACKUP_DIR/uploads" -maxdepth 1 -mindepth 1 -type d ! -name "$TODAY" \
            | sort | tail -1)"
    echo "[3/4] rsync uploads -> $UP_DST${PREV:+  (linked against $(basename "$PREV"))}"
    rsync -a --delete ${PREV:+--link-dest="$PREV"} "$UP_SRC/" "$UP_DST/"
    echo "  OK: $(find "$UP_DST" -type f | wc -l | tr -d ' ') files, $(du -sh "$UP_DST" | cut -f1) apparent"
else
    echo "[3/4] uploads/ not found — skipped"
fi

# ── 4. Retention ───────────────────────────────────────────────────────
echo "[4/4] pruning older than $RETENTION_DAYS days"
find "$BACKUP_DIR/db" -name 'ets-*.sql.gz' -mtime "+$RETENTION_DAYS" -print -delete | sed 's/^/  removed /' || true
find "$BACKUP_DIR/uploads" -maxdepth 1 -mindepth 1 -type d -mtime "+$RETENTION_DAYS" \
    -print -exec rm -rf {} + 2>/dev/null | sed 's/^/  removed /' || true

echo
echo "db snapshots     : $(find "$BACKUP_DIR/db" -name '*.sql.gz' | wc -l | tr -d ' ')"
echo "upload snapshots : $(find "$BACKUP_DIR/uploads" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ')"
echo "backup dir size  : $(du -sh "$BACKUP_DIR" | cut -f1)"
df -h "$BACKUP_DIR" | tail -1
echo "=== done ==="
