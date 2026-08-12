#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Testing ka saara history data mitata hai — production start karne se
#  pehle ek baar chalane ke liye.
#
#  MITAYEGA:
#    activity_logs   (Audit Logs page)
#    attendance      (Attendance page + View Details ka timer/history)
#    screenshots     (Screenshots page ki DB rows)
#    uploads/screenshots/*.enc  (asli encrypted files, disk se)
#
#  NAHI CHHUEGA:
#    employees        — accounts, usernames, passwords, roles
#    employee_configs — shift times, idle threshold, per-employee overrides
#    active_sessions  — abhi logged-in log logged-in hi rahenge
#
#  IDs 1 se dobara shuru honge (RESTART IDENTITY).
#
#  Chalane ka tarika (VPS pe):
#      bash server/scripts/reset_test_data.sh
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Read the database name from the app's own .env, exactly as backup.sh,
# restore_test.sh and purge_screenshots.sh do.
#
# BUG this fixes: this script alone hardcoded a default of "ets". The real
# database is named something else, so every psql call failed with
# `database "ets" does not exist`. The counts printed empty, the backup
# step failed, and `set -e` aborted the run — after the operator had
# already typed DELETE. Nothing was deleted, which is the one thing that
# went right, but the script was unusable and looked like the database was
# missing.
if [ ! -f "$APP_DIR/.env" ]; then
    echo "ERROR: $APP_DIR/.env not found — cannot determine the database name"
    exit 1
fi
set -a; . "$APP_DIR/.env"; set +a
: "${DB_NAME:?DB_NAME missing from .env}"
# The .env is sourced just above, and it is what the server itself reads to
# decide where captures are written. Hardcoding a path here OVERRODE it: on a
# deployment whose UPLOAD_DIR points elsewhere, the database rows went and
# every .enc file stayed on disk — unreadable for ever, because the rows that
# named them had been truncated, and still taking up the space.
UPLOAD_DIR="${UPLOAD_DIR:-$APP_DIR/uploads/screenshots}"
BACKUP_DIR="$HOME/ets-backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/ets-before-reset-$STAMP.sql.gz"

psql_() { sudo -u postgres psql -d "$DB_NAME" -tAc "$1"; }

echo "═══ ABHI KA DATA ═══"
for tbl in activity_logs attendance screenshots; do
    printf "  %-16s %s rows\n" "$tbl" "$(psql_ "SELECT COUNT(*) FROM $tbl;")"
done
echo "  ── ye NAHI mitega ──"
for tbl in employees employee_configs active_sessions; do
    printf "  %-16s %s rows\n" "$tbl" "$(psql_ "SELECT COUNT(*) FROM $tbl;")"
done
if [ -d "$UPLOAD_DIR" ]; then
    echo "  screenshot files $(find "$UPLOAD_DIR" -type f 2>/dev/null | wc -l | tr -d ' ') ($(du -sh "$UPLOAD_DIR" 2>/dev/null | cut -f1))"
fi

echo
echo "⚠️  Ye WAPAS NAHI aayega. Backup pehle liya jayega, lekin"
echo "    restore karna manual kaam hai."
echo
read -r -p 'Aage badhne ke liye "DELETE" type karo: ' CONFIRM
if [ "$CONFIRM" != "DELETE" ]; then
    echo "❌ Cancel. Kuch nahi badla."
    exit 1
fi

# ── 1. BACKUP ──────────────────────────────────────────────────────────
# Server pe abhi koi backup schedule nahi hai, is liye ye reset hi wo
# lamha hai jab backup sabse zyada zaroori hai. Poora DB dump lete hain,
# sirf mitne wali tables nahi — taaki kuch galat ho to sab wapas aa sake.
echo
echo "═══ 1/3  BACKUP ═══"
mkdir -p "$BACKUP_DIR"
sudo -u postgres pg_dump "$DB_NAME" | gzip > "$BACKUP_FILE"
echo "  ✅ $BACKUP_FILE  ($(du -h "$BACKUP_FILE" | cut -f1))"
if [ ! -s "$BACKUP_FILE" ]; then
    echo "  ❌ Backup khaali hai — ruk rahe hain, kuch nahi mitaya."
    exit 1
fi

# ── 2. DB ──────────────────────────────────────────────────────────────
# TRUNCATE (DELETE nahi): tez hai aur RESTART IDENTITY se sequences 1 pe
# reset ho jaate hain, to naya data ID 1 se shuru hoga.
echo
echo "═══ 2/3  DATABASE ═══"
sudo -u postgres psql -d "$DB_NAME" -c \
    "TRUNCATE activity_logs, attendance, screenshots RESTART IDENTITY;"
for tbl in activity_logs attendance screenshots; do
    printf "  %-16s %s rows\n" "$tbl" "$(psql_ "SELECT COUNT(*) FROM $tbl;")"
done

# ── 3. FILES ───────────────────────────────────────────────────────────
# DB rows ke bina ye .enc files kabhi accessible nahi rahengi — sirf
# disk ghera rahengi. In 136MB ka koi kaam nahi.
echo
echo "═══ 3/3  SCREENSHOT FILES ═══"
if [ -d "$UPLOAD_DIR" ]; then
    find "$UPLOAD_DIR" -type f -name '*.enc' -delete
    echo "  ✅ bache hue files: $(find "$UPLOAD_DIR" -type f | wc -l | tr -d ' ')"
else
    echo "  (folder nahi mila — skip)"
fi

echo
echo "═══ HO GAYA ═══"
echo "Backup : $BACKUP_FILE"
echo
echo "Ab har machine pe jahan app install hai, wahan ka LOCAL data bhi"
echo "clear karna zaroori hai — warna un clients ka bina-sync hua purana"
echo "data server pe dobara upload ho jayega."
