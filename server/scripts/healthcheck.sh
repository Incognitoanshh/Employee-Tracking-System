#!/usr/bin/env bash
# Runs every 5 minutes from cron. Writes one line per check to
# ~/ets-health.log and only shouts when something is actually wrong.
#
# Checks: API responding, PM2 process online, PostgreSQL accepting
# connections, disk headroom, and uploads/ growth.
set -uo pipefail

LOG="${ETS_HEALTH_LOG:-$HOME/ets-health.log}"
SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="$(grep -E '^PORT=' "$SERVER_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '\r' || echo 8000)"
PORT="${PORT:-8000}"
TS="$(date "+%Y-%m-%dT%H:%M:%S%z")"
PROBLEMS=()

CODE=$(curl -s -m 10 -o /dev/null -w '%{http_code}' "http://localhost:${PORT}/api/health" || echo 000)
[ "$CODE" = "200" ] || PROBLEMS+=("api=$CODE")

if command -v pm2 >/dev/null 2>&1; then
    PM2_STATUS=$(pm2 jlist 2>/dev/null | python3 -c 'import sys,json
try:
    procs=json.load(sys.stdin)
except Exception:
    print("unknown"); raise SystemExit
for p in procs:
    if p.get("name")=="ets-server":
        print(f'"'"'{p["pm2_env"]["status"]}:{p["pm2_env"].get("restart_time",0)}'"'"'); raise SystemExit
print("missing")' 2>/dev/null || echo unknown)
    case "$PM2_STATUS" in online:*) ;; *) PROBLEMS+=("pm2=$PM2_STATUS") ;; esac
else
    PM2_STATUS="pm2-not-found"
fi

pg_isready -q -h localhost 2>/dev/null && PG=up || { PG=down; PROBLEMS+=("postgres=down"); }

DISK=$(df --output=pcent / 2>/dev/null | tail -1 | tr -dc '0-9' || df / | tail -1 | awk '{print $5}' | tr -dc '0-9')
[ "${DISK:-0}" -ge 80 ] && PROBLEMS+=("disk=${DISK}%")

UP_SIZE=$(du -sm "$SERVER_DIR/uploads" 2>/dev/null | cut -f1 || echo 0)

echo "$TS api=$CODE pm2=$PM2_STATUS pg=$PG disk=${DISK}% uploads=${UP_SIZE}MB" >> "$LOG"

if [ ${#PROBLEMS[@]} -gt 0 ]; then
    echo "$TS ALERT: ${PROBLEMS[*]}" >> "$LOG"
    echo "ALERT: ${PROBLEMS[*]}" >&2
    exit 1
fi

# Keep the log from growing without limit (~2 years of 5-min checks).
[ "$(wc -l < "$LOG")" -gt 200000 ] && tail -100000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit 0
