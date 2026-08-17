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

# READ .env, DO NOT SOURCE IT.
#
# `set -a; . .env` runs the file as shell. Any value containing a space
# becomes a command:
#
#     SMTP_PASS=vxbh qlqm dtrq cgal
#       -> ./.env: line 15: qlqm: command not found
#     SMTP_FROM=Amaze Connect <connect@example.com>
#       -> ./.env: line 16: syntax error near unexpected token `newline'
#
# That is not hypothetical — adding the mail settings broke backup.sh and
# migrate.sh the same evening, and a backup that fails on the day somebody
# edits an unrelated line is the worst kind of fragile.
#
# Reading the line instead means a value can contain spaces, quotes, angle
# brackets or anything else without this script caring. It also means the
# database password and the JWT secret are never put into this shell's
# environment, which healthcheck.sh has always been careful about.
env_value() {
    local key="$1" file="$2" line
    line="$(grep -m1 -E "^[[:space:]]*${key}=" "$file" 2>/dev/null || true)"
    [ -n "$line" ] || return 0
    line="${line#*=}"
    line="${line%$'\r'}"
    # Strip one layer of surrounding quotes, if the value has them.
    case "$line" in
        \"*\") line="${line#\"}"; line="${line%\"}" ;;
        \'*\') line="${line#\'}"; line="${line%\'}" ;;
    esac
    printf '%s' "$line"
}

DB_NAME="$(env_value DB_NAME "$SERVER_DIR/.env")"
DB_USER="$(env_value DB_USER "$SERVER_DIR/.env")"
UPLOAD_DIR="${UPLOAD_DIR:-$(env_value UPLOAD_DIR "$SERVER_DIR/.env")}"
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
#
# BUG FIX: this used to prune upload snapshots with `find -mtime`, which
# deleted the snapshot it had just created, every single run — so uploads
# were never actually backed up.
#
# `rsync -a` preserves the SOURCE directory's mtime on the destination.
# server/uploads/ itself is rarely modified (screenshots land in the
# uploads/screenshots/ subdirectory), so its mtime is old. The fresh
# snapshot inherited that old mtime, `-mtime +14` matched it, and it was
# removed seconds after being written. Reproduced.
#
# Directory mtime is not a trustworthy age signal here. The directory NAME
# is: it is always YYYYMMDD and set by this script.
echo "[4/4] pruning older than $RETENTION_DAYS days"

# DB dumps are plain files that nothing rewrites, so mtime is fine there.
find "$BACKUP_DIR/db" -name 'ets-*.sql.gz' -mtime "+$RETENTION_DAYS" -print -delete \
    | sed 's/^/  removed /' || true

CUTOFF="$(date -d "-$RETENTION_DAYS days" +%Y%m%d 2>/dev/null \
          || date -v-"${RETENTION_DAYS}"d +%Y%m%d)"
for dir in "$BACKUP_DIR/uploads"/*/; do
    [ -d "$dir" ] || continue
    name="$(basename "$dir")"
    # Only touch directories this script created; never guess at anything else.
    case "$name" in
        [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
        *) continue ;;
    esac
    if [ "$name" -lt "$CUTOFF" ]; then
        rm -rf "$dir"
        echo "  removed $dir"
    fi
done

echo
echo "db snapshots     : $(find "$BACKUP_DIR/db" -name '*.sql.gz' | wc -l | tr -d ' ')"
echo "upload snapshots : $(find "$BACKUP_DIR/uploads" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ')"
echo "backup dir size  : $(du -sh "$BACKUP_DIR" | cut -f1)"
df -h "$BACKUP_DIR" | tail -1
echo "=== done ==="
