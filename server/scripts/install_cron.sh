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

echo "Installed:"
crontab -l | sed -n '/── ETS scheduled jobs/,/── end ETS ──/p'
echo
echo "  02:00 daily   database + uploads backup (14-day retention)"
echo "  03:30 daily   purge data past its retention period"
echo "                (periods are set from Configuration, not in the script)"
echo "  every 5 min   health check -> \$HOME/ets-health.log"
echo "  03:00 Sunday  retention purge (old rows)"
echo "  03:30 Sunday  screenshot file purge (orphaned .enc files)"
echo
echo "Next: run the restore test once to prove the backups work —"
echo "  bash $S/restore_test.sh"
