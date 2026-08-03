#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Deletes screenshot files on disk that no longer have a database row.
#
#  WHY THIS EXISTS
#  retention_purge.sql deletes old rows from the `screenshots` table but
#  cannot touch the .enc files on disk. Without this, uploads/ grows
#  forever. At 1000 employees x 8 captures/day x ~200KB that is ~1.5 GB
#  per day — a 107 GB disk fills in about 70 days, after which uploads
#  fail and PostgreSQL cannot write either.
#
#  WHY NOT THE ONE-LINER IN retention_purge.sql
#  That documented approach was:
#      psql ... -c "SELECT file_name FROM screenshots" > /tmp/keep.txt
#      ls | grep -vxFf /tmp/keep.txt | xargs -r rm --
#  If the psql query fails for any reason — wrong DB name, auth failure,
#  server down — keep.txt is EMPTY. `grep -vxFf` against an empty pattern
#  file matches nothing, -v inverts that to everything, and every single
#  screenshot on disk is deleted. Verified. With no backups that is
#  unrecoverable.
#
#  This script refuses to delete anything unless the keep-list was built
#  successfully and looks sane.
#
#  USAGE
#      bash server/scripts/purge_screenshots.sh            # dry run
#      bash server/scripts/purge_screenshots.sh --apply    # actually delete
#
#  CRON (weekly, after the SQL purge)
#      30 3 * * 0 bash /path/to/server/scripts/purge_screenshots.sh --apply \
#                 >> "$HOME/purge-files.log" 2>&1
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPLOAD_DIR="$SERVER_DIR/uploads/screenshots"

# Read DB settings from the app's own .env so this cannot drift from it.
if [ ! -f "$SERVER_DIR/.env" ]; then
    echo "ERROR: $SERVER_DIR/.env not found"; exit 1
fi
set -a; . "$SERVER_DIR/.env"; set +a
: "${DB_NAME:?DB_NAME missing from .env}"
: "${DB_USER:?DB_USER missing from .env}"

if [ ! -d "$UPLOAD_DIR" ]; then
    echo "Nothing to do: $UPLOAD_DIR does not exist"; exit 0
fi

KEEP="$(mktemp)"; ORPHANS="$(mktemp)"
trap 'rm -f "$KEEP" "$ORPHANS"' EXIT

echo "=== Screenshot file purge  ($(date "+%Y-%m-%dT%H:%M:%S%z")) ==="
echo "uploads : $UPLOAD_DIR"
echo "database: $DB_NAME"

# ── 1. Build the keep-list ─────────────────────────────────────────────
# If this query fails, `set -e` aborts here and nothing is deleted.
PGPASSWORD="${DB_PASSWORD:-}" psql -h "${DB_HOST:-localhost}" -p "${DB_PORT:-5432}" \
    -U "$DB_USER" -d "$DB_NAME" -At \
    -c "SELECT file_name FROM screenshots WHERE file_name IS NOT NULL" > "$KEEP"

KEEP_COUNT=$(wc -l < "$KEEP" | tr -d ' ')
DISK_COUNT=$(find "$UPLOAD_DIR" -type f -name '*.enc' | wc -l | tr -d ' ')
echo "rows in DB   : $KEEP_COUNT"
echo "files on disk: $DISK_COUNT"

# ── 2. Safety guards ───────────────────────────────────────────────────
# An empty keep-list means either the query broke or the table really is
# empty. Both look identical here, and one of them deletes everything, so
# refuse and let a human decide.
if [ "$KEEP_COUNT" -eq 0 ] && [ "$DISK_COUNT" -gt 0 ]; then
    echo
    echo "ABORT: the keep-list is empty but there are $DISK_COUNT files on disk."
    echo "       Either the query failed or the screenshots table is empty."
    echo "       Refusing to delete. Check the table before rerunning."
    exit 1
fi

# ── 3. Work out what is orphaned ───────────────────────────────────────
# Compare basenames; a file is an orphan only if its exact name is absent
# from the keep-list.
sort -u "$KEEP" -o "$KEEP"
find "$UPLOAD_DIR" -type f -name '*.enc' -exec basename {} \; \
    | sort -u | comm -23 - "$KEEP" > "$ORPHANS"

ORPHAN_COUNT=$(wc -l < "$ORPHANS" | tr -d ' ')
echo "orphans      : $ORPHAN_COUNT"

if [ "$ORPHAN_COUNT" -eq 0 ]; then
    echo "Nothing to delete."; exit 0
fi

# Deleting a very large share of the files at once usually means something
# went wrong upstream rather than a normal week of retention.
if [ "$DISK_COUNT" -gt 0 ]; then
    PCT=$(( ORPHAN_COUNT * 100 / DISK_COUNT ))
    if [ "$PCT" -gt 90 ] && [ "$APPLY" -eq 1 ]; then
        echo
        echo "ABORT: this would delete ${PCT}% of all screenshot files."
        echo "       That is far more than a normal retention run."
        echo "       Review before rerunning; nothing was deleted."
        exit 1
    fi
fi

BYTES=$(cd "$UPLOAD_DIR" && tr '\n' '\0' < "$ORPHANS" | xargs -0 -r du -ch 2>/dev/null | tail -1 | cut -f1 || echo "?")
echo "would free   : $BYTES"

if [ "$APPLY" -eq 0 ]; then
    echo
    echo "DRY RUN — nothing deleted. Re-run with --apply to delete."
    head -5 "$ORPHANS" | sed 's/^/   /'
    [ "$ORPHAN_COUNT" -gt 5 ] && echo "   … and $((ORPHAN_COUNT - 5)) more"
    exit 0
fi

cd "$UPLOAD_DIR"
tr '\n' '\0' < "$ORPHANS" | xargs -0 -r rm -f --
echo "Deleted $ORPHAN_COUNT files."
echo "remaining    : $(find "$UPLOAD_DIR" -type f -name '*.enc' | wc -l | tr -d ' ')"
df -h "$UPLOAD_DIR" | tail -1
