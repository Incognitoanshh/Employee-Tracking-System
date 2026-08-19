#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  What actually happened on one IST day, per employee.
#
#  WHY THIS EXISTS
#  The scheduler's timezone handling is the part of this system that has
#  broken production twice: once producing 130 captures where 10 were
#  configured, once producing none at all with no error anywhere. Sixty
#  scenarios across four timezones pass in CI, but CI runs one process on
#  one machine. The thing that has never been proven is two REAL machines
#  in different timezones agreeing over a real day.
#
#  Run that test like this:
#    1. Give two employees the same shift and the same screenshots per day
#    2. Sign one in on a Mac set to IST, the other on Windows set to its own
#       timezone — Pacific is the useful one, it is the case that broke
#    3. Leave both signed in for the whole shift
#    4. Run this the next morning
#
#  Both must show the SAME count, and it must equal what was configured.
#  A difference means the machine's own timezone is leaking into the
#  schedule again.
#
#
#  NOTE: this uses `sudo -u postgres`, which needs a terminal to prompt for
#  a password. Over `ssh host 'command'` there is no terminal and it fails
#  with "a terminal is required to read the password". Use `ssh -t`:
#
#      ssh -t $ETS_HOST 'bash .../verify_day.sh'
#
#  ...or run it from an interactive session on the server.
#  Usage:
#      bash server/scripts/verify_day.sh              # yesterday
#      bash server/scripts/verify_day.sh 2026-08-06   # a specific IST day
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -f "$APP_DIR/.env" ]; then
    echo "ERROR: $APP_DIR/.env not found"
    exit 1
fi
set -a; . "$APP_DIR/.env"; set +a
: "${DB_NAME:?DB_NAME missing from .env}"

DAY="${1:-}"
if [ -z "$DAY" ]; then
    DAY="$(sudo -u postgres psql -d "$DB_NAME" -tAc \
        "SELECT (DATE(NOW() AT TIME ZONE 'Asia/Kolkata') - 1)::text")"
fi

echo "═══ IST day $DAY ═══"
echo

sudo -u postgres psql -d "$DB_NAME" <<SQL
\pset border 2
SELECT
    e.employee_id                                        AS "Employee",
    COALESCE(c.shift_start::text, g.shift_start::text, '—')
      || '–' ||
    COALESCE(c.shift_end::text,   g.shift_end::text,   '—')  AS "Shift",
    COALESCE(c.screenshots_per_day, g.screenshots_per_day) AS "Configured",
    COUNT(s.id)                                          AS "Captured",
    CASE
        WHEN COUNT(s.id) = 0 THEN 'none'
        WHEN COUNT(s.id) > COALESCE(c.screenshots_per_day, g.screenshots_per_day)
            THEN 'OVER THE LIMIT'
        WHEN COUNT(s.id) < COALESCE(c.screenshots_per_day, g.screenshots_per_day)
            THEN 'under (partial day?)'
        ELSE 'exact'
    END                                                  AS "Verdict",
    to_char(MIN(s.created_at) AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata', 'HH24:MI')
                                                         AS "First",
    to_char(MAX(s.created_at) AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata', 'HH24:MI')
                                                         AS "Last"
FROM employees e
LEFT JOIN employee_configs c ON c.employee_id = e.employee_id
LEFT JOIN employee_configs g ON g.employee_id IS NULL
LEFT JOIN screenshots s
       ON s.employee_id = e.employee_id
      AND DATE((s.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata') = '$DAY'
WHERE e.role <> 'super_admin'
GROUP BY e.employee_id, c.shift_start, g.shift_start, c.shift_end, g.shift_end,
         c.screenshots_per_day, g.screenshots_per_day
ORDER BY e.employee_id;
SQL

echo
echo "Reading this:"
echo "  exact            — configured number produced, nothing lost or extra"
echo "  under            — normal if they were not signed in for the whole shift"
echo "  OVER THE LIMIT   — the daily cap failed. This must never appear."
echo "  none             — either off that day, or not signed in at all"
echo
echo "For the timezone test, the two machines' Captured columns must MATCH."
echo "First and Last should also fall inside the shift on both."
