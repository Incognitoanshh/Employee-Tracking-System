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
#
#  NOTE: this uses `sudo -u postgres`, which needs a terminal to prompt for
#  a password. Over `ssh host 'command'` there is no terminal and it fails
#  with "a terminal is required to read the password". Use `ssh -t`:
#
#      ssh -t etsadmin@HOST 'bash .../compare_schedules.sh'
#
#  ...or run it from an interactive session on the server.
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

# One psql call, one CTE. The verdict MUST be computed from the same rows
# the table shows.
#
# BUG this fixes: the table took the latest plan per employee, but the
# verdict scanned every planning line of the day. A client reschedules on
# each sign-in and on every config change, each time over a different
# window — so a single client on its own was enough to report "different
# windows", and the verdict the whole test depends on was noise.
sudo -u postgres psql -q -d "$DB_NAME" <<SQL
\pset border 2
CREATE TEMP VIEW latest AS
    SELECT DISTINCT ON (al.employee_id)
           al.employee_id,
           substring(al.activity from '— ([0-9]+) capture')::int  AS planned,
           substring(al.activity from 'between (.*) IST')         AS plan_window,
           al.created_at,
           -- Each client is judged against ITS OWN configured number. Two
           -- machines deliberately set to different counts is a normal way
           -- to run this — what proves the timezone is the WINDOW being
           -- identical across machines, not the counts.
           COALESCE(ec.screenshots_per_day, eg.screenshots_per_day) AS configured
      FROM activity_logs al
      LEFT JOIN employee_configs ec ON ec.employee_id = al.employee_id
      LEFT JOIN employee_configs eg ON eg.employee_id IS NULL
     WHERE al.activity LIKE '%capture(s) planned%'
       AND DATE((al.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')
           = DATE(NOW() AT TIME ZONE 'Asia/Kolkata')
       $FILTER
     ORDER BY al.employee_id, al.created_at DESC;

SELECT
    l.employee_id  AS "Employee",
    l.configured   AS "Configured",
    l.planned      AS "Planned",
    l.plan_window  AS "Window (IST)",
    to_char(l.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata', 'HH24:MI:SS')
                   AS "Planned at"
FROM latest l
ORDER BY l.employee_id;

\pset border 0
\pset tuples_only on
\pset format unaligned
SELECT CASE
    WHEN COUNT(*) = 0 THEN
        'No planning lines today. Turn Verbose logging ON for these employees,
  save, then change a scheduling value so the client re-plans — verbose on
  its own does not trigger one.'
    WHEN bool_or(planned IS DISTINCT FROM configured) THEN
        '❌ A client planned a different number than it is configured for.
   That is a daily-budget fault, whatever the timezones are.'
    WHEN COUNT(*) = 1 THEN
        'Only one client reported — nothing to compare across machines. It did
  plan exactly its configured number, which is correct as far as it goes.'
    WHEN COUNT(DISTINCT plan_window) = 1 THEN
        '✅ Both planned over the SAME IST window, each planning exactly its
   own configured number. Different counts are fine — what has to match
   across machines is the WINDOW.'
    ELSE
        '⚠️  DIFFERENT windows. Fine if they signed in at different times or
   have different shifts. If the shifts match and they signed in together,
   this is a timezone fault.'
END FROM latest;
SQL

echo
echo "Reading this:"
echo "  Configured and Planned should match, on every row."
echo "  Two machines on the same shift should show the SAME window, even"
echo "  though their own clocks are hours apart."
echo
echo "  Confirm again tomorrow with the counts that actually happened:"
echo "      bash server/scripts/verify_day.sh"
