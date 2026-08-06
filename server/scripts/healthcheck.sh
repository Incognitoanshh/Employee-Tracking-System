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
# Read rather than source: .env holds the database password, the JWT secret
# and the encryption key, and this script has no business having them in its
# environment where a subprocess could inherit them.
ETS_NTFY_TOPIC="${ETS_NTFY_TOPIC:-$(grep -E '^ETS_NTFY_TOPIC=' "$SERVER_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r')}"
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

    # ── PUSH THE ALERT SOMEWHERE A PERSON WILL SEE IT ──────────────────
    #
    # Until now an alert only reached this log file, which nobody opens
    # until they are already investigating an outage. A monitor that only
    # tells you after you have noticed is not a monitor.
    #
    # ntfy.sh is used because it is free, needs no account and no API key:
    # you pick an unguessable topic name, install the app, subscribe, and
    # notifications arrive. Set ETS_NTFY_TOPIC in server/.env, e.g.
    #
    #     ETS_NTFY_TOPIC=ets-alerts-8fj3kd92ms
    #
    # The topic IS the secret — anyone who knows it can read the alerts, so
    # keep it long and random and do not put it in a public document. The
    # messages carry no credentials or employee data, only which check
    # failed.
    #
    # NOT FATAL if it fails: an unreachable notification service must never
    # stop the health check from writing its log or from reporting failure
    # through its exit code.
    #
    # Repeats are throttled to one an hour per problem set — a server that
    # is down stays down, and 5-minute checks would send twelve alerts an
    # hour until somebody silenced the whole thing.
    if [ -n "${ETS_NTFY_TOPIC:-}" ]; then
        FINGERPRINT="$(echo "${PROBLEMS[*]}" | tr -d ' ' | tr -c 'a-zA-Z0-9' '_')"
        STAMP_FILE="/tmp/ets-alert-$FINGERPRINT"
        LAST=0
        [ -f "$STAMP_FILE" ] && LAST=$(cat "$STAMP_FILE" 2>/dev/null || echo 0)
        NOW_EPOCH=$(date +%s)
        if [ $((NOW_EPOCH - LAST)) -ge 3600 ]; then
            curl -s -m 10 \
                -H "Title: Amaze ETS alert" \
                -H "Priority: high" \
                -H "Tags: warning" \
                -d "${PROBLEMS[*]}" \
                "https://ntfy.sh/${ETS_NTFY_TOPIC}" >/dev/null 2>&1 \
                && echo "$NOW_EPOCH" > "$STAMP_FILE"
        fi
    fi

    exit 1
fi

# Recovered: clear the throttles so the next failure alerts immediately
# rather than being suppressed by an hour-old stamp from the last one.
rm -f /tmp/ets-alert-* 2>/dev/null

# Keep the log from growing without limit (~2 years of 5-min checks).
[ "$(wc -l < "$LOG")" -gt 200000 ] && tail -100000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit 0
