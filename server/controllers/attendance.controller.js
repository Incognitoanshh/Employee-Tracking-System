const pool = require("../config/db");
const { pageOf, idOf } = require("../utils/request_params");
const { istDate, istToday, isTodayIST } = require("../utils/ist_sql");
// The same grace the employee list uses, so "working" cannot mean two things.
const { HEARTBEAT_GRACE_MINUTES, MAX_SHIFT_HOURS } = require("../utils/presence");
const { attendanceState, shiftState } = require("../utils/attendance_state");
// The single hierarchy rule, so attendance cannot disagree with the rest of
// the panel about who may act on whom.
const { canManage } = require("../middleware/admin.middleware");
const {
    classifyLogin,
    isNonWorkingDay,
    shiftDayOffset,
    shiftIsoDate,
    toMinutes,
} = require("../utils/attendance_status");

// super_admin ko har jagah admin ke barabar (ya usse upar) treat karo.
// BUG: ye checks pehle sirf "admin" dekhte the, is liye super_admin ko
// sirf apna hi data dikhta — poori company ka nahi.
const isElevated = (role) => role === "admin" || role === "super_admin";

/**
 * Attach an on-time / late / day-off status to attendance rows.
 *
 * Everything needed comes from three small queries rather than a join per
 * row: the page holds at most 50 records covering a handful of employees and
 * a narrow band of dates, so this is two round trips regardless of page size.
 *
 * Any failure leaves the rows exactly as they came out of the table, minus
 * the helper columns. The attendance page existed for months without this
 * column and must not start failing to load because a status could not be
 * worked out.
 */
async function annotateAttendance(rows) {
    if (rows.length === 0) return rows;

    const strip = (row) => {
        const { ist_day, ist_minutes, ist_out_minutes, day_first_login,
                elapsed_seconds, ...rest } = row;
        return rest;
    };

    try {
        const employeeIds = [...new Set(rows.map((r) => r.employee_id))];

        const configResult = await pool.query(
            `SELECT employee_id, shift_start, shift_end, weekly_offs, late_grace_minutes
               FROM employee_configs
              WHERE employee_id = ANY($1) OR employee_id IS NULL`,
            [employeeIds]
        );
        const global = configResult.rows.find((r) => r.employee_id === null) || {};
        const byEmployee = new Map(
            configResult.rows
                .filter((r) => r.employee_id !== null)
                .map((r) => [r.employee_id, r])
        );

        // A day either side of the range covers overnight shifts, whose
        // status is decided against the previous day.
        const days = rows.map((r) => r.ist_day).filter(Boolean).sort();
        const holidayResult = await pool.query(
            `SELECT to_char(holiday_date, 'YYYY-MM-DD') AS d
               FROM holidays
              WHERE holiday_date BETWEEN $1::date - 1 AND $2::date + 1`,
            [days[0], days[days.length - 1]]
        );
        const holidays = new Set(holidayResult.rows.map((r) => r.d));

        // APPROVED LEAVE, for the days these rows fall on.
        //
        // A day with leave AND a login is not a contradiction — somebody on
        // half a day works the other half — so this does not replace the
        // status, it is carried alongside it. What it does end is a day being
        // called nothing at all when the reason for it is on record.
        const leaveRows = await pool.query(
            `SELECT l.employee_id, TO_CHAR(day::date,'YYYY-MM-DD') AS d,
                    l.leave_type, l.half_day
               FROM leave_requests l
               CROSS JOIN LATERAL generate_series(l.start_date, l.end_date, '1 day') AS day
              WHERE l.status = 'APPROVED'
                AND l.employee_id = ANY($1)`,
            [[...new Set(rows.map((r) => r.employee_id))]]);
        const leaveByDay = new Map(
            leaveRows.rows.map((r) => [`${r.employee_id}|${r.d}`,
                                       { type: r.leave_type, half: r.half_day }]));

        return rows.map((row) => {
            const config = byEmployee.get(row.employee_id) || global;
            const shiftStart = config.shift_start ? String(config.shift_start) : null;
            const shiftEnd   = config.shift_end   ? String(config.shift_end)   : null;

            const isFirst = Boolean(row.day_first_login
                && String(row.day_first_login) === String(row.login_time));

            const loginMinutes = Number(row.ist_minutes);
            const offset = shiftDayOffset(
                loginMinutes,
                toMinutes(shiftStart) ?? 0,
                toMinutes(shiftEnd) ?? 0
            );
            const shiftDay = offset === 0 ? row.ist_day : shiftIsoDate(row.ist_day, offset);

            const leave = leaveByDay.get(`${row.employee_id}|${row.ist_day}`);

            const grace = config.late_grace_minutes ?? global.late_grace_minutes ?? 10;
            const isDayOff = isNonWorkingDay(
                shiftDay, config.weekly_offs ?? global.weekly_offs, holidays);

            // TWO COLUMNS, TWO QUESTIONS. What happened to the record, and
            // how the shift compared to the one they were meant to work.
            // They used to share a cell, which is how "Not signed out" ended
            // up sitting beside "Signed in again" and reading as one
            // self-contradicting sentence.
            const record = attendanceState(row);
            const shift = shiftState({
                loginMinutes,
                logoutMinutes: row.ist_out_minutes === null
                    || row.ist_out_minutes === undefined
                    ? null : Number(row.ist_out_minutes),
                shiftStart,
                shiftEnd,
                graceMinutes: grace,
                isDayOff,
                leave,
                isFirstOfDay: isFirst,
            });

            return {
                ...strip(row),
                attendance_status: record.status,
                attendance_label:  record.label,
                shift_status:      shift.status,
                shift_label:       shift.label,
                shift_notes:       (shift.notes || []).map((n) => n.label),
                leave_type:        leave ? leave.type : null,
                late_minutes:      shift.late_minutes ?? null,

                // The shift this row was judged against, so an administrator
                // can see WHY it says Late without opening the configuration
                // for that employee.
                shift_window: shiftStart && shiftEnd
                    ? `${String(shiftStart).slice(0, 5)}–${String(shiftEnd).slice(0, 5)}`
                    : null,

                // HOW LONG THIS SHIFT HAS BEEN RUNNING, measured by the
                // server. The page counts up from here rather than working it
                // out from login_time itself: that would mean comparing a
                // timestamp the server stored in UTC against the clock on
                // whichever laptop is looking at it, and a machine five
                // minutes out would show five minutes of work that never
                // happened. One clock, the server's.
                elapsed_seconds: row.elapsed_seconds === null
                    || row.elapsed_seconds === undefined
                    ? null : Math.max(0, Math.round(Number(row.elapsed_seconds))),

                // KEPT, AND DELIBERATELY. An older client reads `status_label`
                // and would show an empty column against a newer server —
                // which is exactly the position every machine is in between
                // the server being deployed and the last laptop being
                // updated. It carries the shift verdict, which is what that
                // column always meant to say.
                status:       shift.status,
                status_label: shift.label,
            };
        });

    } catch (error) {
        console.error("[attendance] status annotation failed:", error.message);
        return rows.map(strip);
    }
}


exports.getAttendance = async (req, res) => {
    try {
        let { employee_id, date, from, to, status } = req.query;
        // Clamped rather than trusted — `req.query.page` is a string, and
        // "-1" made a negative OFFSET, which Postgres refuses with a 500.
        const page   = pageOf(req.query.page);
        const limit  = 50;
        const offset = (page - 1) * limit;

        // SECURITY: non-admin apna data hi dekh sakte hain
        if (!isElevated(req.employee?.role)) {
            employee_id = req.employee?.employee_id;
        }

        const conditions = [];
        const values     = [];
        let   idx        = 1;

        if (employee_id) {
            // A NAME WORKS AS WELL AS AN ID.
            //
            // This was an exact match on the id, so the box labelled
            // "Employee ID" was the only way in: an administrator who knew
            // somebody as "Shailabh" had to go to another page, find the id,
            // come back and type it. The id match stays exact so a full id
            // never drags in a name that happens to contain it.
            conditions.push(
                `(employee_id = $${idx} OR EXISTS (
                     SELECT 1 FROM employees e
                      WHERE e.employee_id = attendance.employee_id
                        AND e.full_name ILIKE '%' || $${idx} || '%'))`);
            values.push(employee_id);
            idx += 1;
        }

        // BUG FIX: admin panel ke Attendance tab me date picker maujood tha
        // lekin server `date` param support hi nahi karta tha — us control ko
        // dabane se kuch hota hi nahi tha. Ab UTC-stored login_time ko IST me
        // convert karke uss din ke records filter hote hain (baaki panel
        // bhi isi pattern se date filter karta hai).
        if (date) {
            conditions.push(
                `${istDate("login_time")} = $${idx++}`
            );
            values.push(date);
        }

        // A RANGE, WHICH IS WHAT ATTENDANCE IS ACTUALLY READ IN.
        //
        // One day at a time meant "last week" was seven searches, and
        // anything longer was not attempted. Both ends are inclusive and
        // either may be given on its own — "everything since the first" is a
        // real question, and so is "everything up to the day they left".
        if (from) {
            conditions.push(`${istDate("login_time")} >= $${idx++}::date`);
            values.push(from);
        }
        if (to) {
            conditions.push(`${istDate("login_time")} <= $${idx++}::date`);
            values.push(to);
        }

        // FILTER BY THE RECORD'S STATE, IN SQL, so paging and the total stay
        // honest. Filtering after the rows were fetched would page over the
        // unfiltered set: "Page 1 of 33" beside four visible rows.
        //
        // ONLY the record's state, not the shift verdict. Late, Early Exit
        // and the rest are worked out in JavaScript against each employee's
        // own shift; expressing them here would mean writing that rule a
        // second time, in another language, where the two can drift apart —
        // and a Late filter that disagrees with the Late column is worse
        // than no filter at all.
        const liveSession = `EXISTS (
            SELECT 1 FROM active_sessions ses
             WHERE ses.employee_id = attendance.employee_id
               AND ses.token IS NOT NULL
               AND ses.last_seen > NOW() - INTERVAL '${HEARTBEAT_GRACE_MINUTES} minutes')`;
        const STATE_FILTERS = {
            active:     `logout_time IS NULL AND ${liveSession}`,
            incomplete: `logout_time IS NULL AND NOT ${liveSession}`,
            completed:  `logout_time IS NOT NULL`,
        };
        if (status && STATE_FILTERS[String(status)]) {
            conditions.push(STATE_FILTERS[String(status)]);
        }

        const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";

        const result = await pool.query(
            `SELECT id, employee_id, login_time, logout_time, total_hours,
                    -- The name, so the list can be read by somebody who does
                    -- not know every employee id by heart. LEFT JOIN: a
                    -- deleted employee's history stays readable rather than
                    -- vanishing from the page.
                    (SELECT e.full_name FROM employees e
                      WHERE e.employee_id = attendance.employee_id)  AS employee_name,
                    -- Only for an open row, and only from the server's clock.
                    CASE WHEN logout_time IS NULL THEN
                        EXTRACT(EPOCH FROM ((NOW() AT TIME ZONE 'UTC') - login_time))
                    END                                              AS elapsed_seconds,
                    ${istDate("login_time")} ::text                    AS ist_day,
                    EXTRACT(HOUR   FROM (login_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata') * 60
                  + EXTRACT(MINUTE FROM (login_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')
                                                                       AS ist_minutes,
                    -- The same figure for the way out, which is what Early
                    -- Exit and Overtime are measured against. Computed in SQL
                    -- for the same reason the login one is: the timezone
                    -- arithmetic stays in one layer instead of being redone,
                    -- differently, in JavaScript.
                    CASE WHEN logout_time IS NULL THEN NULL ELSE
                        EXTRACT(HOUR   FROM (logout_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata') * 60
                      + EXTRACT(MINUTE FROM (logout_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')
                    END                                                AS ist_out_minutes,
                    -- Only the FIRST sign-in of a day can be late. Someone who
                    -- reconnects at 14:00 after a dropped session has not
                    -- arrived five hours late, and flagging it that way would
                    -- make the column worthless within a day.
                    --
                    -- The window runs over the filtered set before LIMIT, so
                    -- this stays correct across pagination.
                    -- ASKED PER ROW, NOT COMPUTED OVER THE WHOLE TABLE.
                    --
                    -- This was MIN(login_time) OVER (PARTITION BY employee,
                    -- day). A window function runs BEFORE ORDER BY and LIMIT,
                    -- so it was computed for every row in the table and all
                    -- but fifty thrown away: on 500,000 rows that was a
                    -- 333ms window over a 241ms sort, for one page. Measured.
                    --
                    -- The lateral below asks the same question only for the
                    -- rows actually returned — fifty index lookups — and the
                    -- two were compared row by row before this was changed.
                    day_window.day_first_login                          AS day_first_login,
                    -- IS THIS OPEN ROW ACTUALLY SOMEBODY WORKING?
                    --
                    -- The page drew "ACTIVE" from logout_time being null and
                    -- asked nothing else, so a row left open by an app that
                    -- was closed without signing out read as somebody at
                    -- their desk — for up to sixteen hours, until the
                    -- abandoned-shift sweep reached it.
                    --
                    -- Meanwhile the employee list, which asks presence, said
                    -- Offline about the same person at the same moment. Two
                    -- screens, one person, opposite answers. Seen with two
                    -- accounts at once: ACTIVE here, "Offline · 11 hr ago"
                    -- there.
                    --
                    -- The same live-session test presence uses, so they
                    -- cannot disagree again.
                    EXISTS (
                        SELECT 1 FROM active_sessions ses
                         WHERE ses.employee_id = attendance.employee_id
                           AND ses.token IS NOT NULL
                           AND ses.last_seen > NOW() - INTERVAL '${HEARTBEAT_GRACE_MINUTES} minutes'
                    )                                                  AS session_live
             FROM attendance
             LEFT JOIN LATERAL (
                 -- The IST day this row belongs to, expressed as a range on
                 -- login_time so an index can serve it. The boundaries are
                 -- IST midnight converted back to UTC, which is what the
                 -- column stores.
                 SELECT MIN(same_day.login_time) AS day_first_login
                   FROM attendance same_day
                  WHERE same_day.employee_id = attendance.employee_id
                    -- QUALIFIED, AND IT HAS TO BE. Inside a lateral,
                    -- an unqualified login_time inside this subquery binds
                    -- to same_day, the INNER table — not to the row being
                    -- annotated. The range became a statement about itself
                    -- and every row read "Extra Session". Caught by
                    -- test_attendance_status, which is exactly what it is for.
                    AND same_day.login_time >=
                        (${istDate("attendance.login_time")}::timestamp
                            AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'UTC'
                    AND same_day.login_time <
                        ((${istDate("attendance.login_time")} + 1)::timestamp
                            AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'UTC'
             ) day_window ON TRUE
             ${where}
             ORDER BY id DESC
             LIMIT $${idx} OFFSET $${idx + 1}`,
            [...values, limit, offset]
        );

        const countResult = await pool.query(
            `SELECT COUNT(*) FROM attendance ${where}`, values
        );

        const data = await annotateAttendance(result.rows);

        return res.json({
            success: true,
            data,
            total:   Number(countResult.rows[0].count),
            page:    Number(page),
        });

    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

exports.loginAttendance = async (req, res) => {
    try {
        let { employee_id, login_time } = req.body || {};

        if (!isElevated(req.employee?.role)) {
            employee_id = req.employee?.employee_id;
        }

        if (!employee_id) {
            return res.status(400).json({ success: false, error: "employee_id required" });
        }

        // Close any open sessions (crash/force-close se pehle wali session
        // jo kabhi properly logout nahi hui — naye login se pehle safety-net
        // ke taur pe band kar do). total_hours yaha bhi NOW() - login_time
        // se compute karo (NULL chhodne ki jagah) — self-consistent rehta
        // hai (isi row ke apne dono timestamps se), display pe "—" ki jagah
        // ek meaningful (best-effort) duration dikhega.
        // CLOSE AND OPEN AS ONE, UNDER A LOCK.
        //
        // These were two separate statements, and two logins arriving at the
        // same moment interleaved: both closed the old row, then both
        // inserted. Measured with six simultaneous calls — two rows left
        // open, and an attendance row that never ends is a shift that never
        // ends. It reaches the timesheet, the attendance report and presence.
        //
        // It is not a far-fetched race: the panel starts a shift on login and
        // the auto-login path does the same, so a relaunch during a flaky
        // connection is enough. The advisory lock is per employee, so two
        // different people never wait for each other.
        const client = await pool.connect();
        let insertedId;
        let resumed = false;
        try {
            await client.query("BEGIN");
            await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [employee_id]);

            // AN OPEN SHIFT THAT IS STILL ALIVE IS RESUMED, NOT REPLACED.
            //
            // This used to close the open row and insert a new one every
            // single time, so one person's day became four or five rows. It
            // was not only untidy: ten calls arriving together left ten rows,
            // nine of them 00:00:00 long, and the day's own history could no
            // longer be read. Measured, on this code, before the change.
            //
            // "Alive" is judged by the last thing the person actually DID —
            // an activity line or a screenshot — never by the session
            // heartbeat, which /auth/login has already refreshed by the time
            // this runs and would therefore call every session alive.
            //
            // A quiet gap longer than the grace period is somebody who left,
            // so that still closes at the last evidence and starts a fresh
            // row. That is what keeps "closed the app at ten, back at six"
            // from billing eight hours, which is the bug this whole block
            // exists for.
            const alive = await client.query(
                `SELECT a.id
                   FROM attendance a
                  WHERE a.employee_id = $1
                    AND a.logout_time IS NULL
                    -- A SHIFT BELONGS TO THE DAY IT STARTED.
                    --
                    -- Resuming was judged only on whether there was recent
                    -- evidence of the person, and an app left running
                    -- overnight keeps producing that. So a shift opened at
                    -- 18:19 was still open the next morning and read
                    -- "Active, 15:39:18" — one row spanning two days, and
                    -- fifteen hours that would go to payroll as worked.
                    --
                    -- Reported from the running app. Same IST day, or it is
                    -- closed at the last evidence and a new one begins.
                    AND ${istDate("a.login_time")} = ${istToday()}
                    AND GREATEST(
                          a.login_time,
                          COALESCE((SELECT MAX(al.created_at) FROM activity_logs al
                                     WHERE al.employee_id = a.employee_id
                                       AND al.created_at >= a.login_time), a.login_time),
                          COALESCE((SELECT MAX(sc.created_at) FROM screenshots sc
                                     WHERE sc.employee_id = a.employee_id
                                       AND sc.created_at >= a.login_time), a.login_time)
                        ) > (NOW() AT TIME ZONE 'UTC') - ($2 || ' minutes')::interval
                  ORDER BY a.id DESC
                  LIMIT 1`,
                [employee_id, String(HEARTBEAT_GRACE_MINUTES)]
            );

            // NOT AN EARLY RETURN. The connection is released by the finally
            // below, and releasing it here as well would hand the same client
            // back to the pool twice — the exact fault found in the migration
            // runner, where it takes a second request arriving at the wrong
            // moment to turn into a crash.
            if (alive.rows.length > 0) {
                insertedId = alive.rows[0].id;
                resumed = true;
                await client.query("COMMIT");
            } else {

            // CLOSED AT THE LAST EVIDENCE, NOT AT THIS MOMENT.
            //
            // This used to stamp NOW(), which meant the gap between somebody
            // closing their app and next signing in was recorded as time
            // worked. A row in the customer's own list read 94:38:22 for
            // exactly that reason — four days, from one login to the next.
            // Even within a single day it was wrong: close the app at ten,
            // sign in at six, and eight hours went on the timesheet.
            //
            // The honest end of an unclosed shift is the last moment there is
            // any sign of the person: their session heartbeat, an activity
            // line, or a screenshot. With none of those it closes at the
            // login itself, recording nothing rather than a fiction.
            await client.query(
                `UPDATE attendance a
                    SET logout_time = ev.ended,
                        total_hours = ev.ended - a.login_time
                   FROM (
                     -- NOT the session heartbeat, here.
                     --
                     -- By the time this runs, /auth/login has already stamped
                     -- active_sessions.last_seen with the current moment — so
                     -- treating it as evidence would put the end of the OLD
                     -- shift at the start of the new one, which is the very
                     -- thing being fixed. Measured: it recorded eight hours
                     -- for one hour of work. Only what the person actually
                     -- did counts here; the sweep in utils/attendance_cleanup
                     -- may use the heartbeat, because nothing has refreshed it
                     -- there.
                     --
                     -- AND THE EVIDENCE MUST BE FROM THE SHIFT'S OWN DAY.
                     --
                     -- Reported from the running app: a shift opened at 18:19
                     -- was closed the next morning at 11:13 and recorded
                     -- 15:56:17 — because the panel had been left running all
                     -- night writing USER IDLE and USER ACTIVE rows, and the
                     -- newest of them was from today. Tomorrow cannot tell us
                     -- when somebody stopped working yesterday, and sixteen
                     -- hours of it would have gone to payroll as worked.
                     --
                     -- Capped at MAX_SHIFT_HOURS as well, which is the rule
                     -- the abandoned-shift sweep already applies.
                     SELECT a2.id,
                            LEAST(
                              GREATEST(
                                a2.login_time,
                                COALESCE((SELECT MAX(al.created_at) FROM activity_logs al
                                           WHERE al.employee_id = a2.employee_id
                                             AND al.created_at >= a2.login_time
                                             AND ${istDate("al.created_at")}
                                                 = ${istDate("a2.login_time")}),
                                         a2.login_time),
                                COALESCE((SELECT MAX(sc.created_at) FROM screenshots sc
                                           WHERE sc.employee_id = a2.employee_id
                                             AND sc.created_at >= a2.login_time
                                             AND ${istDate("sc.created_at")}
                                                 = ${istDate("a2.login_time")}),
                                         a2.login_time)
                              ),
                              a2.login_time + INTERVAL '${MAX_SHIFT_HOURS} hours'
                            ) AS ended
                       FROM attendance a2
                      WHERE a2.employee_id = $1 AND a2.logout_time IS NULL
                   ) ev
                  WHERE a.id = ev.id`,
                [employee_id]
            );

            // login_time is stamped by the server in UTC. The client used to
            // send an IST string, which Postgres stored without a zone.
            const result = await client.query(
                `INSERT INTO attendance (employee_id, login_time)
                 VALUES ($1, (NOW() AT TIME ZONE 'UTC')) RETURNING id`,
                [employee_id]
            );
                insertedId = result.rows[0].id;
                await client.query("COMMIT");
            }
        } catch (error) {
            await client.query("ROLLBACK");
            throw error;
        } finally {
            client.release();
        }

        res.json({ success: true, id: insertedId, resumed });

    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        res.status(500).json({ success: false, message: "Internal server error" });
    }
};

exports.logoutAttendance = async (req, res) => {
    try {
        let { employee_id } = req.body || {};

        if (!isElevated(req.employee?.role)) {
            employee_id = req.employee?.employee_id;
        }

        if (!employee_id) {
            return res.status(400).json({ success: false, error: "employee_id required" });
        }

        // total_hours client se LIYA NAHI jata — client ka local session
        // (SQLite `shifts` row) server ke actual attendance row se DISCONNECT
        // ho sakta hai (e.g. auto-login ke baad purani open server-session
        // continue hoti hai lekin naya chhota local shift row bhi ban jaata
        // hai) — is wajah se client ka duration calculation kabhi bahut
        // chhota (jaise "8 minutes") ho sakta hai jabki actual server session
        // ghanton lambi thi. Server khud NOW() - login_time se authoritative
        // total_hours compute karta hai — dono values USI row se aate hain,
        // isliye kabhi mismatch nahi ho sakta.
        const result = await pool.query(
            `UPDATE attendance
             SET logout_time = (NOW() AT TIME ZONE 'UTC'), total_hours = (NOW() AT TIME ZONE 'UTC') - login_time
             WHERE id = (
                 SELECT id FROM attendance
                 WHERE employee_id = $1 AND logout_time IS NULL
                 ORDER BY id DESC LIMIT 1
             )
             RETURNING id`,
            [employee_id]
        );

        if (result.rows.length === 0) {
            return res.status(404).json({ success: false, error: "No open attendance session found" });
        }

        res.json({ success: true });

    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        res.status(500).json({ success: false, message: "Internal server error" });
    }
};

/**
 * Close a shift somebody forgot to close, or correct one that was closed
 * wrongly — by hand, by an administrator, on the record.
 *
 * THIS IS THE ONE ENDPOINT IN ATTENDANCE THAT REWRITES HISTORY, and the hours
 * it writes are the hours payroll pays on. So:
 *
 *   - only an administrator, and only over people they may manage — the same
 *     canManage rule the rest of the panel uses, so an admin cannot rewrite a
 *     super admin's hours;
 *   - a reason is required, because "who changed this, and why" is the entire
 *     point of keeping a record of it;
 *   - the old value and the new one BOTH go into the audit log, so the change
 *     can be read afterwards rather than merely noticed;
 *   - the new time must be after the shift began, and cannot be in the future.
 *
 * That last rule matters more than it looks. Without it a mistyped year makes
 * a shift of eighty thousand hours; it lands in total_hours, and a payslip is
 * built from it before anybody sees the row.
 */
exports.setCheckout = async (req, res) => {
    try {
        if (!isElevated(req.employee?.role)) {
            return res.status(403).json({
                success: false,
                message: "Only an administrator can change a shift's end time.",
            });
        }

        const id = Number(req.params.id);
        if (!Number.isInteger(id) || id <= 0) {
            return res.status(400).json({ success: false, message: "Bad record id." });
        }

        const reason = String(req.body?.reason || "").trim();
        if (reason.length < 3) {
            return res.status(400).json({
                success: false,
                message: "A reason is required — it is what makes this change readable later.",
            });
        }

        const existing = await pool.query(
            `SELECT a.id, a.employee_id, a.login_time, a.logout_time, e.role
               FROM attendance a
               LEFT JOIN employees e ON e.employee_id = a.employee_id
              WHERE a.id = $1`, [id]);
        if (existing.rows.length === 0) {
            return res.status(404).json({
                success: false, message: "No such attendance record." });
        }
        const row = existing.rows[0];

        const denial = canManage(req.employee, row.employee_id, row.role);
        if (denial) return res.status(403).json({ success: false, message: denial });

        // An empty logout_time means "close it now". A supplied one is IST,
        // as a person typed it, and Postgres converts it — the timezone
        // arithmetic stays in SQL, which is what has kept those bugs confined
        // to one layer in this project.
        const supplied = String(req.body?.logout_time || "").trim();
        let target;
        try {
            const resolved = await pool.query(
                supplied
                    ? `SELECT (($1::timestamp AT TIME ZONE 'Asia/Kolkata')
                                AT TIME ZONE 'UTC')::timestamp AS t`
                    : `SELECT (NOW() AT TIME ZONE 'UTC')::timestamp AS t`,
                supplied ? [supplied] : []);
            target = resolved.rows[0].t;
        } catch (parseError) {
            return res.status(400).json({
                success: false,
                message: "That is not a time this can read. Use 2026-08-17 18:30.",
            });
        }

        // THE TWO GUARDS. Both are cheap and both prevent a number that
        // reaches somebody's pay.
        if (String(target) <= String(row.login_time)) {
            return res.status(400).json({
                success: false,
                message: "The end of a shift cannot be at or before its start.",
            });
        }
        const future = await pool.query(
            `SELECT $1::timestamp > (NOW() AT TIME ZONE 'UTC') + INTERVAL '2 minutes' AS ahead`,
            [target]);
        if (future.rows[0].ahead) {
            return res.status(400).json({
                success: false, message: "That time is in the future.",
            });
        }

        const updated = await pool.query(
            `UPDATE attendance
                SET logout_time = $2::timestamp,
                    total_hours = $2::timestamp - login_time
              WHERE id = $1
              RETURNING logout_time, total_hours`,
            [id, target]);

        // THE OLD VALUE IS IN HERE, not only the new one. A log saying "the
        // checkout was set to 18:30" cannot answer the question anybody
        // actually asks afterwards, which is what it was before somebody
        // changed it.
        const previous = row.logout_time
            ? `was ${String(row.logout_time).slice(0, 19)}`
            : "was still open";
        await pool.query(
            `INSERT INTO activity_logs (employee_id, activity) VALUES ($1, $2)`,
            [row.employee_id,
             `ATTENDANCE CHECKOUT SET : record #${id} ${previous}, `
             + `now ${String(updated.rows[0].logout_time).slice(0, 19)} UTC `
             + `: by ${req.employee.employee_id} : ${reason}`]
        ).catch((auditError) => {
            // The change happened; say so in the log even if the log itself
            // could not be written, rather than failing a completed edit.
            console.error("[attendance] audit write failed:", auditError.message);
        });

        return res.json({ success: true, data: updated.rows[0] });

    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        res.status(500).json({ success: false, message: "Internal server error" });
    }
};
