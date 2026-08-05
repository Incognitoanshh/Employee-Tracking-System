const pool = require("../config/db");
const { istDate } = require("../utils/ist_sql");
const {
    classifyLogin,
    isNonWorkingDay,
    shiftDayOffset,
    shiftIsoDate,
    toMinutes,
} = require("../utils/attendance_status");

/**
 * Attendance summary over a date range.
 *
 * Everything here already existed in the database and had never been added
 * up. The Attendance page answers "what happened on this row"; this answers
 * "how did this person do over the month", which is the question payroll
 * actually asks.
 *
 * WHY THE DAYS ARE WALKED IN JAVASCRIPT
 * Absence is the absence of a row, so it cannot be selected — it has to be
 * derived by walking every date in the range and asking whether anything
 * happened. Doing that in SQL means generate_series joined against three
 * tables and the weekly-off rules expressed twice, once per language. The
 * rules already live in attendance_status.js and are already tested there,
 * so the days are walked here and the database is asked only for facts:
 * per-day aggregates, configs, holidays, employees. Four queries, regardless
 * of how many days or employees are asked for.
 *
 * WHAT IS DELIBERATELY NOT COUNTED
 * Idle time. The client records USER IDLE and USER ACTIVE as separate
 * events, so a total can only be had by pairing them up — and a crash, a
 * network drop or a logout leaves a pair permanently open. The number would
 * be wrong by an unknown amount, and a wrong idle figure in a payroll report
 * is worse than no idle figure at all. It needs the client to accumulate a
 * daily total before it can be reported honestly.
 */

/** Whole days between two ISO dates, inclusive. */
function daysBetween(fromIso, toIso) {
    const from = new Date(`${fromIso}T00:00:00Z`);
    const to = new Date(`${toIso}T00:00:00Z`);
    return Math.round((to - from) / 86400000) + 1;
}

const MAX_RANGE_DAYS = 366;
const ISO_DATE = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;

exports.getAttendanceReport = async (req, res) => {
    const { from, to, employee_id } = req.query;

    if (!ISO_DATE.test(String(from || "")) || !ISO_DATE.test(String(to || ""))) {
        return res.status(400).json({
            success: false,
            message: "from and to are required, in YYYY-MM-DD format",
        });
    }
    if (from > to) {
        return res.status(400).json({ success: false, message: "from must not be after to" });
    }
    const span = daysBetween(from, to);
    if (span > MAX_RANGE_DAYS) {
        // A year is already more than any report is read for, and without a
        // ceiling one request can walk a decade for a thousand people.
        return res.status(400).json({
            success: false,
            message: `Range is ${span} days — the maximum is ${MAX_RANGE_DAYS}`,
        });
    }

    try {
        const one = employee_id && employee_id !== "all" ? employee_id : null;

        // ── the people ──────────────────────────────────────────────────
        // Super admins are excluded for the same reason they get no
        // screenshots and no idle tracking: they are the owner, not a
        // tracked employee. Including them would report the person reading
        // the report as absent every weekend.
        const employees = await pool.query(
            `SELECT employee_id, username, full_name, role,
                    ${istDate("created_at")}::text AS joined_on
               FROM employees
              WHERE role <> 'super_admin'
                ${one ? "AND employee_id = $1" : ""}
              ORDER BY employee_id`,
            one ? [one] : []
        );
        if (employees.rows.length === 0) {
            return res.json({ success: true, from, to, rows: [] });
        }
        const ids = employees.rows.map((e) => e.employee_id);

        // ── per employee, per IST day: first sign-in and hours ───────────
        const perDay = await pool.query(
            `SELECT employee_id,
                    ${istDate("login_time")}::text AS ist_day,
                    MIN(login_time) AS first_login,
                    MIN(EXTRACT(HOUR   FROM (login_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata') * 60
                      + EXTRACT(MINUTE FROM (login_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')) AS first_minutes,
                    COALESCE(SUM(EXTRACT(EPOCH FROM total_hours)), 0) AS seconds
               FROM attendance
              WHERE employee_id = ANY($1)
                AND ${istDate("login_time")} BETWEEN $2::date AND $3::date
              GROUP BY employee_id, ${istDate("login_time")}`,
            [ids, from, to]
        );
        const days = new Map();
        for (const row of perDay.rows) {
            days.set(`${row.employee_id}|${row.ist_day}`, row);
        }

        // ── screenshots per employee over the range ─────────────────────
        const shots = await pool.query(
            `SELECT employee_id, COUNT(*)::int AS n
               FROM screenshots
              WHERE employee_id = ANY($1)
                AND ${istDate("created_at")} BETWEEN $2::date AND $3::date
              GROUP BY employee_id`,
            [ids, from, to]
        );
        const shotsByEmployee = new Map(shots.rows.map((r) => [r.employee_id, r.n]));

        // Idle comes from idle_daily, which the client accumulates as time
        // passes. It is NOT derived from the IDLE/ACTIVE events in
        // activity_logs — pairing those leaves a pair open on every crash and
        // produces a total that is wrong by an unknown amount.
        //
        // A day with no row means the client never reported one (an older
        // build, or it never ran that day), which is different from a day
        // with zero idle time. Days present are counted so the report can say
        // when the figure is incomplete rather than quietly under-reporting.
        const idle = await pool.query(
            `SELECT employee_id,
                    COALESCE(SUM(idle_seconds), 0)::bigint AS seconds,
                    COUNT(*)::int AS days_reported
               FROM idle_daily
              WHERE employee_id = ANY($1) AND day BETWEEN $2::date AND $3::date
              GROUP BY employee_id`,
            [ids, from, to]
        );
        const idleByEmployee = new Map(
            idle.rows.map((r) => [r.employee_id,
                { seconds: Number(r.seconds), days: r.days_reported }])
        );

        // ── configs, with the usual fallback to the global row ──────────
        const configs = await pool.query(
            `SELECT employee_id, shift_start, shift_end, weekly_offs, late_grace_minutes
               FROM employee_configs
              WHERE employee_id = ANY($1) OR employee_id IS NULL`,
            [ids]
        );
        const global = configs.rows.find((r) => r.employee_id === null) || {};
        const configByEmployee = new Map(
            configs.rows.filter((r) => r.employee_id !== null)
                .map((r) => [r.employee_id, r])
        );

        // A day either side, because an overnight shift is judged against the
        // day it started.
        const holidayRows = await pool.query(
            `SELECT to_char(holiday_date, 'YYYY-MM-DD') AS d
               FROM holidays
              WHERE holiday_date BETWEEN $1::date - 1 AND $2::date + 1`,
            [from, to]
        );
        const holidays = new Set(holidayRows.rows.map((r) => r.d));

        // ── walk the range ──────────────────────────────────────────────
        const rows = employees.rows.map((employee) => {
            const config = configByEmployee.get(employee.employee_id) || global;
            const shiftStart = config.shift_start ? String(config.shift_start) : null;
            const shiftEnd = config.shift_end ? String(config.shift_end) : null;
            const weeklyOffs = config.weekly_offs ?? global.weekly_offs ?? "";
            const grace = config.late_grace_minutes ?? global.late_grace_minutes ?? 10;

            let working = 0, present = 0, absent = 0, late = 0, offDays = 0;
            let lateMinutes = 0, seconds = 0;
            const absentDates = [];

            for (let i = 0; i < span; i += 1) {
                const day = shiftIsoDate(from, i);

                // Somebody who joined on the 20th was not absent on the 5th.
                // Without this every new hire opens with a month of absences
                // in their first report.
                if (employee.joined_on && day < employee.joined_on) continue;

                if (isNonWorkingDay(day, weeklyOffs, holidays)) {
                    offDays += 1;
                    continue;
                }
                working += 1;

                const record = days.get(`${employee.employee_id}|${day}`);
                if (!record) {
                    absent += 1;
                    absentDates.push(day);
                    continue;
                }

                present += 1;
                seconds += Number(record.seconds) || 0;

                const loginMinutes = Number(record.first_minutes);
                const offset = shiftDayOffset(
                    loginMinutes, toMinutes(shiftStart) ?? 0, toMinutes(shiftEnd) ?? 0
                );
                const shiftDay = offset === 0 ? day : shiftIsoDate(day, offset);
                const verdict = classifyLogin({
                    loginMinutes,
                    shiftStart,
                    shiftEnd,
                    graceMinutes: grace,
                    isDayOff: isNonWorkingDay(shiftDay, weeklyOffs, holidays),
                });
                if (verdict.status === "late") {
                    late += 1;
                    lateMinutes += verdict.late_minutes;
                }
            }

            const hours = seconds / 3600;
            return {
                employee_id:   employee.employee_id,
                username:      employee.username,
                full_name:     employee.full_name || employee.username,
                role:          employee.role,
                working_days:  working,
                present_days:  present,
                absent_days:   absent,
                off_days:      offDays,
                late_days:     late,
                late_minutes:  lateMinutes,
                total_hours:   Number(hours.toFixed(2)),
                // Averaged over days actually worked, not over the range.
                // Dividing by working days would drag the figure down for
                // anyone on approved leave and make it read like a
                // performance problem.
                avg_hours:     present ? Number((hours / present).toFixed(2)) : 0,
                screenshots:   shotsByEmployee.get(employee.employee_id) || 0,
                idle_hours:    Number(((idleByEmployee.get(employee.employee_id)?.seconds || 0) / 3600).toFixed(2)),
                // How many of the days they were present actually reported an
                // idle figure. Anything short of present_days means the total
                // covers only part of the range — worth saying rather than
                // presenting a partial number as complete.
                idle_days_reported: idleByEmployee.get(employee.employee_id)?.days || 0,
                // Capped: the point is to show which days, not to ship an
                // unbounded list into a table cell.
                absent_dates:  absentDates.slice(0, 40),
                shift:         shiftStart && shiftEnd
                    ? `${String(shiftStart).slice(0, 5)}–${String(shiftEnd).slice(0, 5)}`
                    : "—",
            };
        });

        return res.json({ success: true, from, to, days: span, rows });

    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};
