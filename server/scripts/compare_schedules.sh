#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Compare what each client PLANNED, side by side.
#
#  verify_day.sh answers the timezone question after a full day. This
#  answers it within a minute of both clients signing in, because the
#  scheduler writes its plan to the audit log the moment it makes one:
#
#      SchedulerService: in-shift — 20 capture(s) planned between
#      06 Aug 09:12 and 06 Aug 17:48 IST (0/20 used today)
#
#  Two machines given the same shift and the same daily count must produce
#  the SAME number over the SAME window, whatever their own clocks say. A
#  difference here is the machine's timezone leaking into the schedule —
#  the fault that once produced 130 captures where 10 were configured.
#
#  Getting a quick answer does not replace the full day. A plan can be
#  right at 09:00 and still drift at the midnight rollover, which is what
#  verify_day.sh checks the next morning. Run both.
#
#  REQUIRES verbose logging ON for the employees being compared —
#  Configuration → Advanced → Verbose logging. Without it the scheduler's
#  planning lines never reach the server and this shows nothing.
#
#  Usage:
#      bash server/scripts/compare_schedules.sh              # today, everyone
#      bash server/scripts/compare_schedules.sh EMP002 AMZ004
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -f "$APP_DIR/.env" ]; then
    echo "ERROR: $APP_DIR/.env not found"
    exit 1
fi
set -a; . "$APP_DIR/.env"; set +a
: "${DB_NAME:?DB_NAME missing from .env}"

FILTER=""
if [ $# -gt 0 ]; then
    LIST=""
    for id in "$@"; do
        LIST="${LIST:+$LIST,}'$id'"
    done
    FILTER="AND al.employee_id IN ($LIST)"
fi

echo "═══ WHAT EACH CLIENT PLANNED TODAY (IST) ═══"
echo

sudo -u postgres psql -d "$DB_NAME" <<SQL
\pset border 2
WITH latest AS (
    SELECT DISTINCT ON (al.employee_id)
           al.employee_id,
           al.activity,
           al.created_at
      FROM activity_logs al
     WHERE al.activity LIKE '%capture(s) planned%'
       AND DATE((al.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')
           = DATE(NOW() AT TIME ZONE 'Asia/Kolkata')
       $FILTER
     ORDER BY al.employee_id, al.created_at DESC
)
SELECT
    l.employee_id                                              AS "Employee",
    COALESCE(c.screenshots_per_day, g.screenshots_per_day)     AS "Configured",
    -- "… — 20 capture(s) planned between …"
    substring(l.activity from '— ([0-9]+) capture')            AS "Planned",
    substring(l.activity from 'between (.*) IST')              AS "Window (IST)",
    to_char(l.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata', 'HH24:MI:SS')
                                                               AS "Planned at"
FROM latest l
LEFT JOIN employee_configs c ON c.employee_id = l.employee_id
LEFT JOIN employee_configs g ON g.employee_id IS NULL
ORDER BY l.employee_id;
SQL

echo
sudo -u postgres psql -d "$DB_NAME" -tAc "
SELECT CASE
    WHEN COUNT(*) = 0 THEN
        'No planning lines today. Turn on Verbose logging for these employees
  in Configuration → Advanced, then have them sign out and back in.'
    WHEN COUNT(DISTINCT substring(activity from '— ([0-9]+) capture')) = 1
     AND COUNT(DISTINCT substring(activity from 'between (.*) IST')) = 1
        THEN '✅ Every client planned the SAME count over the SAME window.'
    WHEN COUNT(DISTINCT substring(activity from '— ([0-9]+) capture')) > 1
        THEN '❌ Clients planned DIFFERENT counts. If their shift and daily
   number are identical, this is a timezone fault — do not ship.'
    ELSE '⚠️  Same count, different windows. Expected if they signed in at
   different times or have different shifts; a timezone fault if not.'
END
FROM activity_logs al
WHERE al.activity LIKE '%capture(s) planned%'
  AND DATE((al.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')
      = DATE(NOW() AT TIME ZONE 'Asia/Kolkata')
  $FILTER;
"

echo
echo "Reading this:"
echo "  Configured and Planned should match, on every row."
echo "  Two machines on the same shift should show the SAME window, even"
echo "  though their own clocks are hours apart."
echo
echo "  Confirm again tomorrow with the counts that actually happened:"
echo "      bash server/scripts/verify_day.sh"
