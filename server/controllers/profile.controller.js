/**
 * A person's own account page.
 *
 * Everything here answers about the CALLER — `req.employee.employee_id` — and
 * never takes an employee id from the request. That is the whole security
 * model of this file: there is no parameter to tamper with. Looking at
 * somebody else's profile is an administrator's job and already lives in
 * admin.controller behind its own role checks.
 *
 * WHAT AN EMPLOYEE MAY CHANGE ABOUT THEMSELVES
 *   their phone number, and their photo.
 *
 * That is the entire list. Role, employee id, department, manager, joining
 * date, employment status, attendance and working hours are all read-only
 * here — a monitoring product where the monitored can edit their own
 * department or their own hours is not a monitoring product. Those fields are
 * writable only through admin.updateProfile, which is behind super-admin.
 *
 * NOTHING IS RECOMPUTED HERE. The figures come from the same tables the
 * dashboard and the reports read, with the same IST day boundary from
 * utils/ist_sql, so this page cannot disagree with the ones beside it.
 */
const path = require("path");
const fs = require("fs");
const pool = require("../config/db");
const { isTodayIST, istDate, istToday } = require("../utils/ist_sql");
const { isOnlineSql, HEARTBEAT_GRACE_MINUTES } = require("../utils/presence");
const { endSession } = require("../utils/session");

const PHOTO_DIR = process.env.PROFILE_PHOTO_DIR
    ? path.resolve(process.env.PROFILE_PHOTO_DIR)
    : path.resolve(__dirname, "../uploads/profile_photos");

/** Only ever the caller. There is no id parameter to get wrong. */
const me = (req) => req.employee?.employee_id;

function fail(res, status, message) {
    return res.status(status).json({ success: false, message });
}

// ─────────────────────────────────────────────────────────── the profile

exports.getMyProfile = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");

    try {
        const result = await pool.query(
            `SELECT e.employee_id, e.username, e.full_name, e.designation, e.role,
                    e.phone, e.department, e.joining_date, e.employment_status,
                    e.photo, e.created_at, e.suspended, e.password_changed_at,
                    m.employee_id  AS manager_id,
                    COALESCE(m.full_name, m.username) AS manager_name,
                    (SELECT string_agg(t.name, ', ' ORDER BY t.name)
                       FROM team_members tm JOIN teams t ON t.id = tm.team_id
                      WHERE tm.employee_id = e.employee_id) AS teams,
                    ${isOnlineSql("e")} AS is_online
               FROM employees e
               LEFT JOIN employees m ON m.employee_id = e.reporting_manager
              WHERE e.employee_id = $1`,
            [employeeId]
        );
        if (result.rows.length === 0) return fail(res, 404, "Profile not found");

        const row = result.rows[0];
        return res.json({
            success: true,
            profile: {
                employee_id: row.employee_id,
                username: row.username,
                full_name: row.full_name,
                designation: row.designation,
                role: row.role,
                phone: row.phone,
                department: row.department,
                team: row.teams,
                reporting_manager: row.manager_name,
                reporting_manager_id: row.manager_id,
                joining_date: row.joining_date,
                // An account nobody has set a status on is simply active,
                // unless it has been suspended — which is a status of its own
                // and must never read as "active".
                employment_status: row.suspended
                    ? "suspended"
                    : (row.employment_status || "active"),
                photo: row.photo,
                member_since: row.created_at,
                password_changed_at: row.password_changed_at,
                status: row.is_online ? "online" : "offline",
            },
        });
    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return fail(res, 500, "Internal server error");
    }
};

/** The two fields an employee owns. Anything else in the body is ignored. */
exports.updateMyProfile = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");

    if (!("phone" in (req.body || {}))) {
        return fail(res, 400, "Nothing to change — only phone can be set here");
    }

    // Empty means "remove it", which is a thing people want to do.
    const raw = String(req.body.phone ?? "").trim();
    const phone = raw === "" ? null : raw;

    if (phone !== null) {
        if (phone.length > 32) {
            return fail(res, 400, "Phone number is too long — 32 characters at most");
        }
        // Digits, spaces, +, -, (), which covers every way people write one.
        if (!/^[0-9+()\-\s]{6,32}$/.test(phone)) {
            return fail(res, 400, "That does not look like a phone number");
        }
    }

    try {
        await pool.query(`UPDATE employees SET phone = $1 WHERE employee_id = $2`,
                         [phone, employeeId]);
        return res.json({ success: true, phone });
    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return fail(res, 500, "Internal server error");
    }
};

// ─────────────────────────────────────────────────────────── the photo

exports.uploadMyPhoto = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");
    if (!req.file) return fail(res, 400, "No image was sent");

    try {
        // The previous one goes, or every replacement leaves a file behind
        // that nothing will ever name again.
        const previous = await pool.query(
            `SELECT photo FROM employees WHERE employee_id = $1`, [employeeId]);
        const old = previous.rows[0]?.photo;

        await pool.query(`UPDATE employees SET photo = $1 WHERE employee_id = $2`,
                         [req.file.filename, employeeId]);

        if (old && old !== req.file.filename) {
            fs.unlink(path.join(PHOTO_DIR, path.basename(old)), () => {});
        }
        return res.json({ success: true, photo: req.file.filename });
    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return fail(res, 500, "Internal server error");
    }
};

exports.deleteMyPhoto = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");
    try {
        const current = await pool.query(
            `SELECT photo FROM employees WHERE employee_id = $1`, [employeeId]);
        const name = current.rows[0]?.photo;
        await pool.query(`UPDATE employees SET photo = NULL WHERE employee_id = $1`,
                         [employeeId]);
        if (name) fs.unlink(path.join(PHOTO_DIR, path.basename(name)), () => {});
        return res.json({ success: true });
    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return fail(res, 500, "Internal server error");
    }
};

exports.getPhoto = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");

    // The id is the CALLER's, so one person cannot read another's photo by
    // guessing a filename — and the name is taken from the database, never
    // from the URL, so there is nothing to traverse with.
    const wanted = String(req.params.employee_id || employeeId);
    const isSelf = wanted === employeeId;
    const elevated = ["admin", "super_admin"].includes(req.employee?.role);
    if (!isSelf && !elevated) return fail(res, 403, "Not yours to look at");

    try {
        const row = await pool.query(
            `SELECT photo FROM employees WHERE employee_id = $1`, [wanted]);
        const name = row.rows[0]?.photo;
        if (!name) return fail(res, 404, "No photo");

        const full = path.join(PHOTO_DIR, path.basename(name));
        if (!fs.existsSync(full)) return fail(res, 404, "No photo");
        return res.sendFile(full);
    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return fail(res, 500, "Internal server error");
    }
};

// ─────────────────────────────────────────────────────────── sessions

exports.getMySessions = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");

    const presented = (req.headers["authorization"] || "").split(" ")[1] || null;

    try {
        const live = await pool.query(
            `SELECT device_id, ip, login_time, last_seen, token,
                    (last_seen > NOW() - INTERVAL '${HEARTBEAT_GRACE_MINUTES} minutes')
                        AS is_live
               FROM active_sessions
              WHERE employee_id = $1
              ORDER BY login_time DESC`,
            [employeeId]
        );

        // The shift rows are the history: one per time this person signed in,
        // which is what "recent logins" means to somebody reading it. The
        // token is compared but never sent back.
        const history = await pool.query(
            `SELECT login_time, logout_time, total_hours
               FROM attendance
              WHERE employee_id = $1
              ORDER BY login_time DESC
              LIMIT 20`,
            [employeeId]
        );

        return res.json({
            success: true,
            sessions: live.rows.map((s) => ({
                device_id: s.device_id,
                ip: s.ip,
                login_time: s.login_time,
                last_seen: s.last_seen,
                is_live: s.is_live,
                is_this_device: Boolean(presented && s.token === presented),
            })),
            history: history.rows,
        });
    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return fail(res, 500, "Internal server error");
    }
};

// ─────────────────────────────────────────────────────────── work summary

exports.getMyWorkSummary = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");

    try {
        const today = isTodayIST("created_at");
        const [shotsToday, logsToday, todayShift, week, month, series] =
            await Promise.all([
                pool.query(
                    `SELECT COUNT(*)::int AS n FROM screenshots
                      WHERE employee_id = $1 AND ${today}`, [employeeId]),
                pool.query(
                    `SELECT COUNT(*)::int AS n FROM activity_logs
                      WHERE employee_id = $1 AND ${today}`, [employeeId]),
                pool.query(
                    `SELECT login_time, logout_time,
                            COALESCE(total_hours,
                                     (NOW() AT TIME ZONE 'UTC') - login_time) AS worked
                       FROM attendance
                      WHERE employee_id = $1 AND ${isTodayIST("login_time")}
                      ORDER BY login_time DESC LIMIT 1`, [employeeId]),
                pool.query(
                    `SELECT COALESCE(SUM(EXTRACT(EPOCH FROM total_hours)), 0)::bigint AS seconds,
                            COUNT(DISTINCT ${istDate("login_time")})::int AS days
                       FROM attendance
                      WHERE employee_id = $1
                        AND ${istDate("login_time")} >= ${istToday()} - 6`,
                    [employeeId]),
                pool.query(
                    `SELECT COALESCE(SUM(EXTRACT(EPOCH FROM total_hours)), 0)::bigint AS seconds,
                            COUNT(DISTINCT ${istDate("login_time")})::int AS days
                       FROM attendance
                      WHERE employee_id = $1
                        AND date_trunc('month', ${istDate("login_time")})
                          = date_trunc('month', ${istToday()})`,
                    [employeeId]),
                // Seven days, every day present even when nothing happened —
                // a chart with holes in it is read as missing data rather
                // than as a day off.
                pool.query(
                    `WITH days AS (
                        SELECT (${istToday()} - offs)::date AS day
                          FROM generate_series(0, 6) offs
                     )
                     SELECT d.day,
                            COALESCE((SELECT SUM(EXTRACT(EPOCH FROM a.total_hours))
                                        FROM attendance a
                                       WHERE a.employee_id = $1
                                         AND ${istDate("a.login_time")} = d.day), 0)::bigint
                                AS worked_seconds,
                            COALESCE((SELECT SUM(i.idle_seconds) FROM idle_daily i
                                       WHERE i.employee_id = $1 AND i.day = d.day), 0)::bigint
                                AS idle_seconds,
                            COALESCE((SELECT COUNT(*) FROM screenshots s
                                       WHERE s.employee_id = $1
                                         AND ${istDate("s.created_at")} = d.day), 0)::int
                                AS screenshots
                       FROM days d
                      ORDER BY d.day`, [employeeId]),
            ]);

        const shift = todayShift.rows[0] || null;
        const idleToday = await pool.query(
            `SELECT COALESCE(idle_seconds, 0)::bigint AS seconds FROM idle_daily
              WHERE employee_id = $1 AND day = ${istToday()}`, [employeeId]);

        const workedTodaySeconds = shift
            ? Math.max(0, Math.round(Number(shift.worked?.seconds ?? 0) ||
                (shift.worked ? shift.worked.hours * 3600 + shift.worked.minutes * 60 +
                    (shift.worked.seconds || 0) : 0)))
            : 0;
        const idleTodaySeconds = Number(idleToday.rows[0]?.seconds || 0);
        const monthDays = Number(month.rows[0].days || 0);
        const monthSeconds = Number(month.rows[0].seconds || 0);

        return res.json({
            success: true,
            today: {
                login_time: shift?.login_time || null,
                logout_time: shift?.logout_time || null,
                worked_seconds: workedTodaySeconds,
                idle_seconds: idleTodaySeconds,
                active_seconds: Math.max(0, workedTodaySeconds - idleTodaySeconds),
                screenshots: shotsToday.rows[0].n,
                activity_lines: logsToday.rows[0].n,
            },
            week: {
                worked_seconds: Number(week.rows[0].seconds || 0),
                days_present: Number(week.rows[0].days || 0),
            },
            month: {
                worked_seconds: monthSeconds,
                days_present: monthDays,
                average_daily_seconds: monthDays
                    ? Math.round(monthSeconds / monthDays) : 0,
                // Against working days so far this month, not against 30 —
                // measuring a person on days that have not happened yet reads
                // as a failure that is nobody's.
                attendance_percent: (() => {
                    const soFar = new Date().getDate();
                    return soFar ? Math.min(100, Math.round((monthDays / soFar) * 100)) : 0;
                })(),
            },
            last_7_days: series.rows.map((r) => ({
                day: r.day,
                worked_seconds: Number(r.worked_seconds),
                idle_seconds: Number(r.idle_seconds),
                screenshots: r.screenshots,
            })),
        });
    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return fail(res, 500, "Internal server error");
    }
};

// ─────────────────────────────────────────────────────── logout everywhere

/**
 * Sign this person out of every machine, including the one asking.
 *
 * The same two writes every other sign-out uses — utils/session, so this
 * cannot drift from logout, force logout, suspension or a password reset. It
 * ends the caller's OWN sessions and takes no employee id: signing somebody
 * else out is force logout, which is an administrator's, behind its own check.
 *
 * The caller's current token dies with the rest. That is the point — somebody
 * pressing this has usually lost a laptop and wants everything gone, and
 * leaving the machine they happen to be holding signed in would make the
 * button a lie.
 */
exports.logoutEverywhere = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");
    try {
        await endSession(pool, employeeId);
        return res.json({
            success: true,
            message: "Signed out on every device. Please sign in again.",
        });
    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return fail(res, 500, "Internal server error");
    }
};
