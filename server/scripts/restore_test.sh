#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Restores the latest backup into a THROWAWAY database and checks it.
#
#  A backup nobody has restored is not a backup. This proves the dump can
#  actually be loaded and that the data inside it is intact.
#
#  Safe to run on production: it creates a temporary database
#  (ets_restore_test_<pid>), restores into that, verifies, and drops it.
#  The live database is never touched — the script aborts if the target
#  name ever resolves to the live DB_NAME.
#
#  USAGE
#      bash server/scripts/restore_test.sh
#
#  Run this after setting up backups, and again whenever the schema
#  changes meaningfully.
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${ETS_BACKUP_DIR:-$HOME/ets-backups}"

[ -f "$SERVER_DIR/.env" ] || { echo "ERROR: $SERVER_DIR/.env not found"; exit 1; }
set -a; . "$SERVER_DIR/.env"; set +a
: "${DB_NAME:?DB_NAME missing from .env}"
: "${DB_USER:?DB_USER missing from .env}"

TEST_DB="ets_restore_test_$$"
if [ "$TEST_DB" = "$DB_NAME" ]; then
    echo "ABORT: test database name collides with the live database"; exit 1
fi

DUMP="$(find "$BACKUP_DIR/db" -name 'ets-*.sql.gz' 2>/dev/null | sort | tail -1)"
[ -n "$DUMP" ] || { echo "ERROR: no backups found in $BACKUP_DIR/db"; exit 1; }

psql_() { PGPASSWORD="${DB_PASSWORD:-}" psql -h "${DB_HOST:-localhost}" -p "${DB_PORT:-5432}" -U "$DB_USER" "$@"; }
cleanup() { psql_ -d postgres -c "DROP DATABASE IF EXISTS $TEST_DB" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "=== Restore test  $(date "+%Y-%m-%dT%H:%M:%S%z") ==="
echo "backup : $DUMP  ($(du -h "$DUMP" | cut -f1))"
echo "age    : $(( ( $(date +%s) - $(stat -c %Y "$DUMP" 2>/dev/null || stat -f %m "$DUMP") ) / 3600 )) hours old"
echo "target : $TEST_DB  (throwaway)"
echo

echo "[1/3] creating test database"
psql_ -d postgres -c "CREATE DATABASE $TEST_DB" >/dev/null

echo "[2/3] restoring"
gzip -dc "$DUMP" | psql_ -d "$TEST_DB" -v ON_ERROR_STOP=1 -q >/dev/null

echo "[3/3] verifying contents"
FAIL=0
for tbl in employees employee_configs attendance activity_logs screenshots active_sessions; do
    n=$(psql_ -d "$TEST_DB" -tAc "SELECT COUNT(*) FROM $tbl" 2>/dev/null || echo "MISSING")
    printf "  %-18s %s\n" "$tbl" "$n"
    [ "$n" = "MISSING" ] && FAIL=1
done

# The employees table must never be empty — that would mean nobody can log
# in after a restore, which is the one thing this backup exists to prevent.
EMP=$(psql_ -d "$TEST_DB" -tAc "SELECT COUNT(*) FROM employees" 2>/dev/null || echo 0)
SUPER=$(psql_ -d "$TEST_DB" -tAc "SELECT COUNT(*) FROM employees WHERE role='super_admin'" 2>/dev/null || echo 0)
echo
[ "$EMP" -gt 0 ]   || { echo "  FAIL: employees table is empty"; FAIL=1; }
[ "$SUPER" -gt 0 ] || { echo "  FAIL: no super_admin in restored data — nobody could administer the system"; FAIL=1; }

if [ "$FAIL" -eq 0 ]; then
    echo "RESTORE TEST PASSED — $EMP employees, $SUPER super admin(s)"
else
    echo "RESTORE TEST FAILED — see above"; exit 1
fi
