#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  The backup you would actually restore from.
#
#  WHAT HAPPENED. The nightly dump failed for THIRTY-EIGHT NIGHTS and
#  nobody knew. scripts/backup.sh read DB_NAME and DB_USER out of .env,
#  but took the password from its own environment — PGPASSWORD="${DB_PASSWORD:-}"
#  — and the environment under cron, which is the only place this script
#  ever runs, has no such variable. It worked only while the database
#  accepted a connection without one. The day .env was given a password
#  (17 Aug 2026) the 02:00 run stopped at "fe_sendauth: no password
#  supplied", and so did every run after it.
#
#  AND IT LOOKED FINE. The shell creates the .gz file the moment the
#  redirection is set up, before pg_dump has said a word — so each failed
#  night left a 20-byte archive with that morning's date on it. `ls` showed
#  a fresh backup every day for five weeks.
#
#  So two things are checked here, and the first one is the one that was
#  missing: the password the script hands to pg_dump comes from .env. That
#  is asserted by INTERCEPTING pg_dump rather than by connecting, because a
#  developer's Postgres usually trusts local connections and would hand
#  back a perfectly good dump with no password at all — proving nothing.
#
#  The health check is here too. A backup that can fail silently will, and
#  the thing that lets it is that nobody reads ~/ets-backup.log until they
#  are already restoring.
#
#  Run:  bash server/tests/test_backup_sh.sh
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# SPACE, on purpose — the app's own directory has one, and it is what broke
# migrate.sh in production. See test_migrate_sh.sh.
WORK="$(mktemp -d)/app copy"
failures=0

check() {
    if [ "$2" = "1" ]; then
        echo "  PASS  $1"
    else
        echo "  FAIL  $1${3:+  — $3}"
        failures=$((failures + 1))
    fi
}

cleanup() { rm -rf "$(dirname "$WORK")"; }
trap cleanup EXIT

mkdir -p "$WORK/server/scripts" "$WORK/server/tests" "$WORK/server/uploads" "$WORK/fakebin" "$WORK/backups"
cp "$ROOT/server/scripts/backup.sh" "$WORK/server/scripts/"
cp "$ROOT/server/scripts/healthcheck.sh" "$WORK/server/scripts/"

# A password with a space in it, because .env is READ and not sourced, and
# the whole reason it is read is that values like this exist.
SECRET="dbpass with space"
cat > "$WORK/server/.env" <<EOF
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=ets_backup_test
DB_USER=ets_backup_user
DB_PASSWORD=$SECRET
PORT=8000
EOF

# ── a pg_dump of our own, so this tests the script and not Postgres ─────
cat > "$WORK/fakebin/pg_dump" <<'EOF'
#!/usr/bin/env bash
# Records what it was given, then prints something that passes the
# script's own verification: five tables and the completion marker.
{
    echo "PGPASSWORD=[${PGPASSWORD-<unset>}]"
    echo "ARGS=[$*]"
} > "$FAKE_PG_DUMP_RECORD"
[ "${FAKE_PG_DUMP_FAIL:-0}" = "1" ] && { echo "pg_dump: error: refused" >&2; exit 1; }
for n in 1 2 3 4 5; do echo "CREATE TABLE public.t$n (id integer);"; done
echo "-- PostgreSQL database dump complete"
EOF
chmod +x "$WORK/fakebin/pg_dump"

echo
echo "The password the dump is given"

RECORD="$WORK/record.txt"
# env -u: the bug was that these came from the environment. Under cron they
# are not there, and here they must not be either.
env -u DB_PASSWORD -u DB_HOST -u DB_PORT -u PGPASSWORD \
    PATH="$WORK/fakebin:$PATH" \
    FAKE_PG_DUMP_RECORD="$RECORD" \
    ETS_BACKUP_DIR="$WORK/backups" \
    bash "$WORK/server/scripts/backup.sh" > "$WORK/run1.log" 2>&1
RC=$?

check "the backup runs" "$([ $RC -eq 0 ] && echo 1 || echo 0)" \
      "exit $RC; $(tail -2 "$WORK/run1.log" | tr '\n' ' ')"
check "and pg_dump is handed the password from .env, not an empty one" \
      "$(grep -qF "PGPASSWORD=[$SECRET]" "$RECORD" && echo 1 || echo 0)" \
      "$(grep PGPASSWORD "$RECORD" 2>/dev/null || echo 'nothing recorded')"
check "with the host, port, user and database it names too" \
      "$(grep -qF -- "-h 127.0.0.1 -p 5432 -U ets_backup_user -d ets_backup_test" "$RECORD" && echo 1 || echo 0)" \
      "$(grep ARGS "$RECORD" 2>/dev/null || echo 'nothing recorded')"

DUMP="$(ls -t "$WORK/backups/db"/*.sql.gz 2>/dev/null | head -1)"
check "a dump is written" "$([ -n "$DUMP" ] && echo 1 || echo 0)"
if [ -n "$DUMP" ]; then
    SIZE=$(stat -c %s "$DUMP" 2>/dev/null || stat -f %z "$DUMP")
    check "and it is a real one, not a 20-byte gzip" \
          "$([ "$SIZE" -gt 100 ] && echo 1 || echo 0)" "${SIZE}B"
fi

echo
echo "When the database refuses"

rm -f "$WORK/backups/db"/*.sql.gz
env -u DB_PASSWORD -u PGPASSWORD \
    PATH="$WORK/fakebin:$PATH" \
    FAKE_PG_DUMP_RECORD="$RECORD" \
    FAKE_PG_DUMP_FAIL=1 \
    ETS_BACKUP_DIR="$WORK/backups" \
    bash "$WORK/server/scripts/backup.sh" > "$WORK/run2.log" 2>&1
RC=$?

check "the script fails rather than reporting success" \
      "$([ $RC -ne 0 ] && echo 1 || echo 0)" "exit $RC"
LEFT="$(ls "$WORK/backups/db"/*.sql.gz 2>/dev/null | wc -l | tr -d ' ')"
# THE ONE THAT HID FIVE WEEKS OF FAILURE. An empty archive with today's
# date on it is worse than no file at all: it is a failure that looks like
# a backup from every angle except opening it.
check "and leaves NO file behind to be mistaken for a backup" \
      "$([ "$LEFT" = "0" ] && echo 1 || echo 0)" "$LEFT file(s) left"
check "saying what went wrong" \
      "$(grep -qi "FAILED" "$WORK/run2.log" && echo 1 || echo 0)" \
      "$(tail -2 "$WORK/run2.log" | tr '\n' ' ')"

echo
echo "The health check notices"

HEALTH_LOG="$WORK/health.log"
run_health() {
    : > "$HEALTH_LOG"
    ETS_BACKUP_DIR="$WORK/backups" ETS_HEALTH_LOG="$HEALTH_LOG" \
        bash "$WORK/server/scripts/healthcheck.sh" > /dev/null 2>&1
    cat "$HEALTH_LOG"
}

mkdir -p "$WORK/backups/db"
rm -f "$WORK/backups/db"/*.sql.gz

OUT="$(run_health)"
check "nothing at all to restore from is a problem" \
      "$(echo "$OUT" | grep -q "backup=none" && echo 1 || echo 0)" \
      "$(echo "$OUT" | tail -1)"

# 20 bytes: an empty gzip, which is exactly what the broken nights left.
printf '\037\213\010\000\000\000\000\000\000\003\003\000\000\000\000\000\000\000\000\000' \
    > "$WORK/backups/db/ets-empty.sql.gz"
OUT="$(run_health)"
check "an empty archive is caught by its size, not trusted by its date" \
      "$(echo "$OUT" | grep -q "backup-empty" && echo 1 || echo 0)" \
      "$(echo "$OUT" | tail -1)"

rm -f "$WORK/backups/db"/*.sql.gz
head -c 40000 /dev/urandom > "$WORK/backups/db/ets-old.sql.gz"
# Two days back: a night was missed, and then another.
touch -t "$(date -d '2 days ago' +%Y%m%d%H%M 2>/dev/null || date -v-2d +%Y%m%d%H%M)" \
      "$WORK/backups/db/ets-old.sql.gz"
OUT="$(run_health)"
check "a dump too old to be last night's is a problem" \
      "$(echo "$OUT" | grep -q "backup-age" && echo 1 || echo 0)" \
      "$(echo "$OUT" | tail -1)"

head -c 40000 /dev/urandom > "$WORK/backups/db/ets-fresh.sql.gz"
OUT="$(run_health)"
check "and last night's real dump says nothing" \
      "$(echo "$OUT" | grep -qE "backup-(none|empty|age)" && echo 0 || echo 1)" \
      "$(echo "$OUT" | tail -1)"
check "while still reporting it, so the log shows what was there" \
      "$(echo "$OUT" | grep -q "backup=" && echo 1 || echo 0)" \
      "$(echo "$OUT" | tail -1)"

echo
if [ "$failures" -gt 0 ]; then
    echo "$failures failure(s)"
    exit 1
fi
echo "all backup script checks passed"
exit 0
