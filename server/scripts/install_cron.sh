#!/usr/bin/env bash
# Installs all ETS scheduled jobs. Idempotent — safe to re-run; it
# removes any previous ETS entries before adding the current set.
set -euo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$SERVER_DIR/scripts"

for f in backup.sh healthcheck.sh purge_screenshots.sh; do
    [ -f "$S/$f" ] || { echo "ERROR: $S/$f not found"; exit 1; }
done

NEW=$(cat <<CRON
# ── ETS scheduled jobs (managed by install_cron.sh) ──
0 2 * * *   bash "$S/backup.sh" >> "\$HOME/ets-backup.log" 2>&1
*/5 * * * * bash "$S/healthcheck.sh" >> /dev/null 2>&1
30 3 * * *  bash "$S/purge_old_data.sh" >> "\$HOME/ets-purge.log" 2>&1
0 3 * * 0   cd "$SERVER_DIR" && set -a && . ./.env && set +a && PGPASSWORD="\$DB_PASSWORD" psql -h "\${DB_HOST:-localhost}" -U "\$DB_USER" -d "\$DB_NAME" -f migrations/retention_purge.sql >> "\$HOME/ets-purge.log" 2>&1
30 3 * * 0  bash "$S/purge_screenshots.sh" --apply >> "\$HOME/ets-purge-files.log" 2>&1
# ── end ETS ──
CRON
)

( crontab -l 2>/dev/null | sed '/── ETS scheduled jobs/,/── end ETS ──/d'; echo "$NEW" ) | crontab -

# ── PM2 log rotation ──────────────────────────────────────────────────
#
# The server logs a line per request, and every client polls for chat and
# for the dashboard. Twenty employees is a few requests a second, which is
# hundreds of thousands of lines a day — and PM2 never rotates its own log
# files. Left alone it grows without limit on the SAME DISK that holds the
# encrypted screenshots, so the first thing to break is screenshot uploads,
# with a disk-full error nobody is watching for.
#
# Not fatal to skip, so a failure here does not fail the whole install.
if command -v pm2 >/dev/null 2>&1; then
    if pm2 install pm2-logrotate >/dev/null 2>&1; then
        pm2 set pm2-logrotate:max_size 20M      >/dev/null 2>&1 || true
        pm2 set pm2-logrotate:retain 14         >/dev/null 2>&1 || true
        pm2 set pm2-logrotate:compress true     >/dev/null 2>&1 || true
        pm2 set pm2-logrotate:rotateInterval "0 0 * * *" >/dev/null 2>&1 || true
        echo "PM2 log rotation: 20 MB per file, 14 kept, compressed."
    else
        echo "WARNING: could not install pm2-logrotate — PM2 logs will grow without limit."
        echo "         Run this by hand:  pm2 install pm2-logrotate"
    fi
else
    echo "WARNING: pm2 not found, so log rotation was not set up."
fi

echo "Installed:"
crontab -l | sed -n '/── ETS scheduled jobs/,/── end ETS ──/p'
echo
echo "  02:00 daily   database + uploads backup (14-day retention)"
echo "  03:30 daily   purge data past its retention period"
echo "                (periods are set from Configuration, not in the script)"
echo "  every 5 min   health check -> \$HOME/ets-health.log"
echo "  03:00 Sunday  retention purge (old rows)"
echo "  03:30 Sunday  screenshot file purge (orphaned .enc files)"
echo "  on rotation   PM2 logs capped at 20 MB x 14, compressed"
echo
echo "Next: run the restore test once to prove the backups work —"
echo "  bash $S/restore_test.sh"
