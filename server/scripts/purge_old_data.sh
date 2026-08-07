#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Delete data past its retention period.
#
#  WHY THIS EXISTS
#  Nothing was ever purged. retention_purge.sql had 90 and 180 days written
#  into it and was never added to cron, so activity_logs and screenshots
#  grew without limit. At two employees that is a few megabytes; at a
#  thousand, ten captures a day each, it is a couple of gigabytes a day
#  until the disk fills and the server stops answering.
#
#  Periods come from app_settings, which the super admin sets from
#  Configuration. Nothing is hardcoded here — a number in a script is a
#  number nobody can change without ssh.
#
#  WHAT IT REMOVES
#    activity_logs   older than log_retention_days
#    screenshots     older than screenshot_retention_days, DB rows AND the
#                    encrypted files on disk
#    attendance      older than attendance_retention_days
#
#  Screenshot files are deleted by name, read from the rows before they go.
#  Deleting rows without the files is how the last orphan pile started.
#
#  Usage:
#      bash server/scripts/purge_old_data.sh            # do it
#      bash server/scripts/purge_old_data.sh --dry-run  # just count
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -f "$APP_DIR/.env" ]; then
    echo "ERROR: $APP_DIR/.env not found"
    exit 1
fi
set -a; . "$APP_DIR/.env"; set +a
: "${DB_NAME:?DB_NAME missing from .env}"

UPLOAD_DIR="${UPLOAD_DIR:-$APP_DIR/uploads/screenshots}"
psql_() { sudo -u postgres psql -d "$DB_NAME" -tAc "$1"; }

setting() {
    local value
    value="$(psql_ "SELECT value FROM app_settings WHERE key = '$1'" 2>/dev/null || echo "")"
    echo "${value:-$2}"
}

LOG_DAYS="$(setting log_retention_days 90)"
AUDIT_DAYS="$(setting audit_log_retention_days 730)"
SHOT_DAYS="$(setting screenshot_retention_days 180)"
ATT_DAYS="$(setting attendance_retention_days 730)"

# A retention of zero would mean "delete everything", and a misread or empty
# setting must never become that. Refuse rather than guess.
for pair in "logs:$LOG_DAYS" "audit:$AUDIT_DAYS" "screenshots:$SHOT_DAYS" "attendance:$ATT_DAYS"; do
    name="${pair%%:*}"; days="${pair#*:}"
    if ! [[ "$days" =~ ^[0-9]+$ ]] || [ "$days" -lt 7 ]; then
        echo "ERROR: $name retention reads '$days' — refusing to run."
        echo "       Set it from Configuration, or check app_settings."
        exit 1
    fi
done

# Administrative actions are kept apart. One period cannot serve both: short
# enough to keep the table small deletes the record of who reset whose
# password, and long enough to keep that keeps millions of idle flips.
AUDIT_MATCH="$(cd "$APP_DIR/.." && node -e '
const { auditRowsSql } = require("./server/utils/audit_events");
process.stdout.write(auditRowsSql("activity"));
')"

echo "═══ RETENTION ═══"
echo "  activity logs : $LOG_DAYS days"
echo "  admin actions : $AUDIT_DAYS days  (password resets, deletions, role changes)"
echo "  screenshots   : $SHOT_DAYS days"
echo "  attendance    : $ATT_DAYS days"
echo

echo "═══ WHAT IS PAST IT ═══"
OLD_LOGS="$(psql_ "SELECT COUNT(*) FROM activity_logs
                    WHERE created_at < NOW() - INTERVAL '$LOG_DAYS days'
                      AND NOT ($AUDIT_MATCH)")"
OLD_AUDIT="$(psql_ "SELECT COUNT(*) FROM activity_logs
                     WHERE created_at < NOW() - INTERVAL '$AUDIT_DAYS days'
                       AND ($AUDIT_MATCH)")"
OLD_SHOTS="$(psql_ "SELECT COUNT(*) FROM screenshots WHERE created_at < NOW() - INTERVAL '$SHOT_DAYS days'")"
OLD_ATT="$(psql_ "SELECT COUNT(*) FROM attendance WHERE login_time < NOW() - INTERVAL '$ATT_DAYS days'")"
printf "  activity_logs %8s\n  admin actions %8s\n  screenshots   %8s\n  attendance    %8s\n" \
    "$OLD_LOGS" "$OLD_AUDIT" "$OLD_SHOTS" "$OLD_ATT"

if [ "$DRY_RUN" = "1" ]; then
    echo
    echo "  (dry run — nothing deleted)"
    exit 0
fi

if [ "$OLD_LOGS" = "0" ] && [ "$OLD_AUDIT" = "0" ] && [ "$OLD_SHOTS" = "0" ] && [ "$OLD_ATT" = "0" ]; then
    echo
    echo "  Nothing to do."
    exit 0
fi

echo
echo "═══ DELETING ═══"

# Filenames first: once the rows are gone there is no way to find the files
# they pointed at, and they would sit on disk forever.
FILES="$(psql_ "SELECT file_name FROM screenshots
                 WHERE created_at < NOW() - INTERVAL '$SHOT_DAYS days'
                   AND file_name IS NOT NULL")"

sudo -u postgres psql -d "$DB_NAME" -v ON_ERROR_STOP=1 <<SQL
BEGIN;
-- Noise on the short period, administrative actions on the long one.
DELETE FROM activity_logs
 WHERE created_at < NOW() - INTERVAL '$LOG_DAYS days'
   AND NOT ($AUDIT_MATCH);
DELETE FROM activity_logs
 WHERE created_at < NOW() - INTERVAL '$AUDIT_DAYS days'
   AND ($AUDIT_MATCH);
DELETE FROM screenshots   WHERE created_at < NOW() - INTERVAL '$SHOT_DAYS days';
DELETE FROM attendance    WHERE login_time < NOW() - INTERVAL '$ATT_DAYS days';
COMMIT;
SQL

REMOVED=0
if [ -n "$FILES" ]; then
    while IFS= read -r name; do
        [ -z "$name" ] && continue
        # basename only — never trust a stored value to build a path.
        target="$UPLOAD_DIR/$(basename "$name")"
        if [ -f "$target" ]; then rm -f "$target" && REMOVED=$((REMOVED + 1)); fi
    done <<< "$FILES"
fi
echo "  screenshot files removed: $REMOVED"

# Files with no row at all — from an interrupted purge, or an employee
# deleted by a build that predates file cleanup.
ORPHANS=0
if [ -d "$UPLOAD_DIR" ]; then
    while IFS= read -r path; do
        [ -z "$path" ] && continue
        base="$(basename "$path")"
        hit="$(psql_ "SELECT 1 FROM screenshots WHERE file_name = '$(printf '%s' "$base" | sed "s/'/''/g")' LIMIT 1")"
        if [ -z "$hit" ]; then rm -f "$path" && ORPHANS=$((ORPHANS + 1)); fi
    done < <(find "$UPLOAD_DIR" -type f -name '*.enc' -mtime +1 2>/dev/null)
fi
echo "  orphaned files removed  : $ORPHANS"

# ── chat attachments nobody ever sent ──────────────────────────────────────
#  A chat file is uploaded BEFORE the message that carries it, and claimed
#  when that message is sent. Somebody who attaches a file and then closes the
#  panel, or loses their connection mid-send, leaves one behind: a row with no
#  message_seq and a file on disk that nothing will ever reference again.
#
#  Nothing else cleans these up, so without this they accumulate for the life
#  of the installation — quietly, because each one is small and none of them
#  is an error.
#
#  A day's grace: an upload in progress right now has no message yet either,
#  and deleting it would break a send that was working perfectly.
CHAT_DIR="${CHAT_UPLOAD_DIR:-$APP_DIR/uploads/chat}"
CHAT_ORPHANS=0
STALE="$(psql_ "SELECT COALESCE(string_agg(stored_name, E'\n'), '')
                  FROM attachments
                 WHERE message_seq IS NULL
                   AND created_at < NOW() - INTERVAL '1 day'" 2>/dev/null || true)"
if [ -n "$STALE" ] && [ -d "$CHAT_DIR" ]; then
    while IFS= read -r stored; do
        [ -z "$stored" ] && continue
        target="$CHAT_DIR/$(basename "$stored")"
        [ -f "$target" ] && rm -f "$target"
        CHAT_ORPHANS=$((CHAT_ORPHANS + 1))
    done <<< "$STALE"
    psql_ "DELETE FROM attachments
            WHERE message_seq IS NULL
              AND created_at < NOW() - INTERVAL '1 day'" >/dev/null
fi
echo "  unsent chat files       : $CHAT_ORPHANS"

sudo -u postgres psql -d "$DB_NAME" -q -c "VACUUM ANALYZE activity_logs;" \
                                      -c "VACUUM ANALYZE screenshots;" 2>/dev/null || true

echo
echo "═══ LEFT ═══"
printf "  activity_logs %8s\n  screenshots   %8s\n  attendance    %8s\n" \
    "$(psql_ 'SELECT COUNT(*) FROM activity_logs')" \
    "$(psql_ 'SELECT COUNT(*) FROM screenshots')" \
    "$(psql_ 'SELECT COUNT(*) FROM attendance')"
echo "  uploads on disk: $(du -sh "$UPLOAD_DIR" 2>/dev/null | cut -f1 || echo '?')"
