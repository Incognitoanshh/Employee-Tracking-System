const pool = require("../config/db");
const { istDate, istToday, isTodayIST } = require("../utils/ist_sql");
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
        const { ist_day, ist_minutes, day_first_login, ...rest } = row;
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

        return rows.map((row) => {
            const config = byEmployee.get(row.employee_id) || global;
            const shiftStart = config.shift_start ? String(config.shift_start) : null;
            const shiftEnd   = config.shift_end   ? String(config.shift_end)   : null;

            // A reconnect mid-day is not an arrival.
            const isFirst = row.day_first_login
                && String(row.day_first_login) === String(row.login_time);
            if (!isFirst) {
                return { ...strip(row), status: "reconnect", status_label: "—", late_minutes: null };
            }

            const loginMinutes = Number(row.ist_minutes);
            const offset = shiftDayOffset(
                loginMinutes,
                toMinutes(shiftStart) ?? 0,
                toMinutes(shiftEnd) ?? 0
            );
            const shiftDay = offset === 0 ? row.ist_day : shiftIsoDate(row.ist_day, offset);

            const verdict = classifyLogin({
                loginMinutes,
                shiftStart,
                shiftEnd,
                graceMinutes: config.late_grace_minutes ?? global.late_grace_minutes ?? 10,
                isDayOff: isNonWorkingDay(shiftDay, config.weekly_offs ?? global.weekly_offs, holidays),
            });

            return {
                ...strip(row),
                status:       verdict.status,
                status_label: verdict.label,
                late_minutes: verdict.late_minutes,
            };
        });

    } catch (error) {
        console.error("[attendance] status annotation failed:", error.message);
        return rows.map(strip);
    }
}


exports.getAttendance = async (req, res) => {
    try {
        let { employee_id, date, page = 1 } = req.query;
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
            conditions.push(`employee_id = $${idx++}`);
            values.push(employee_id);
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

        const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";

        const result = await pool.query(
            `SELECT id, employee_id, login_time, logout_time, total_hours,
                    ${istDate("login_time")} ::text                    AS ist_day,
                    EXTRACT(HOUR   FROM (login_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata') * 60
                  + EXTRACT(MINUTE FROM (login_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')
                                                                       AS ist_minutes,
                    -- Only the FIRST sign-in of a day can be late. Someone who
                    -- reconnects at 14:00 after a dropped session has not
                    -- arrived five hours late, and flagging it that way would
                    -- make the column worthless within a day.
                    --
                    -- The window runs over the filtered set before LIMIT, so
                    -- this stays correct across pagination.
                    MIN(login_time) OVER (
                        PARTITION BY employee_id, ${istDate("login_time")}
                    )                                                  AS day_first_login
             FROM attendance ${where}
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
        try {
            await client.query("BEGIN");
            await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [employee_id]);
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
                     SELECT a2.id,
                            GREATEST(
                                a2.login_time,
                                COALESCE((SELECT MAX(al.created_at) FROM activity_logs al
                                           WHERE al.employee_id = a2.employee_id
                                             AND al.created_at >= a2.login_time),
                                         a2.login_time),
                                COALESCE((SELECT MAX(sc.created_at) FROM screenshots sc
                                           WHERE sc.employee_id = a2.employee_id
                                             AND sc.created_at >= a2.login_time),
                                         a2.login_time)
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
        } catch (error) {
            await client.query("ROLLBACK");
            throw error;
        } finally {
            client.release();
        }

        res.json({ success: true, id: insertedId });

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
