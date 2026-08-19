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
 *   their phone number, their email address, and their photo.
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
const crypto = require("crypto");
const mailer = require("../utils/mailer");

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
                    e.phone, e.email, e.email_verified_at, e.department,
                    e.joining_date, e.employment_status,
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
                email: row.email,
                // Whether the address was PROVED, not merely typed. The page
                // shows the difference, because an unverified address is not
                // something anything should be sent to.
                email_verified: Boolean(row.email_verified_at),
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

    const body = req.body || {};
    const wantsPhone = "phone" in body;
    const wantsEmail = "email" in body;
    if (!wantsPhone && !wantsEmail) {
        return fail(res, 400,
            "Nothing to change — only phone and email can be set here");
    }

    // Empty means "remove it", which is a thing people want to do.
    const rawPhone = String(body.phone ?? "").trim();
    const phone = rawPhone === "" ? null : rawPhone;
    const rawEmail = String(body.email ?? "").trim();
    const email = rawEmail === "" ? null : rawEmail;

    if (wantsPhone && phone !== null) {
        if (phone.length > 32) {
            return fail(res, 400, "Phone number is too long — 32 characters at most");
        }
        // Digits, spaces, +, -, (), which covers every way people write one.
        if (!/^[0-9+()\-\s]{6,32}$/.test(phone)) {
            return fail(res, 400, "That does not look like a phone number");
        }
    }

    if (wantsEmail && email !== null) {
        if (email.length > 255) {
            return fail(res, 400, "Email address is too long — 255 characters at most");
        }
        // SHAPE ONLY, AND NOT MUCH OF IT. This checks that there is something,
        // then an @, then something with a dot in it — which catches the
        // typos people actually make ("ansh@gmail", a name with no @ at all)
        // and refuses nothing valid. A stricter pattern is where real
        // addresses get rejected; the only thing that proves an address works
        // is sending to it, and nothing here claims this one is verified.
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
            return fail(res, 400, "That does not look like an email address");
        }
    }

    try {
        // Only what was sent is touched, so saving a phone cannot wipe an
        // email the page never showed.
        // CHANGING THE ADDRESS UN-PROVES IT. What was verified was the old
        // one, and carrying the tick across to a new address would make the
        // tick mean nothing at all — which is worse than not having it, since
        // something would eventually be sent on the strength of it.
        //
        // Setting it to the SAME value it already had is not a change, and
        // must not throw away a verification somebody has already done: that
        // is what saving the phone number on a page carrying both fields
        // would otherwise do every time.
        await pool.query(
            `UPDATE employees
                SET phone = CASE WHEN $1::boolean THEN $2 ELSE phone END,
                    email = CASE WHEN $3::boolean THEN $4 ELSE email END,
                    email_verified_at = CASE
                        WHEN $3::boolean AND $4 IS DISTINCT FROM email
                        THEN NULL ELSE email_verified_at END
              WHERE employee_id = $5`,
            [wantsPhone, phone, wantsEmail, email, employeeId]);
        // The pending code goes with it, for the same reason.
        if (wantsEmail) {
            await pool.query(
                `DELETE FROM email_verifications
                  WHERE employee_id = $1 AND email IS DISTINCT FROM $2`,
                [employeeId, email]);
        }
        return res.json({ success: true, phone, email });
    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return fail(res, 500, "Internal server error");
    }
};

// ────────────────────────────────────────────── proving the email address

// Six digits. Long enough that guessing needs a loop rather than luck, short
// enough to be read off a phone and typed without a mistake — which matters
// more than it sounds, because a code somebody mistypes gets requested again.
const CODE_LENGTH = 6;
const CODE_VALID_MINUTES = 10;
// A code that lives all day is a code sitting in an inbox somebody else may
// read later. Ten minutes is long enough for slow mail and short enough that
// a forgotten message is worth nothing.
const MAX_ATTEMPTS = 5;
// Six digits is a million possibilities against a person and nothing at all
// against a script. Five wrong answers ends this code — not the account, and
// not the ability to request another one, because locking somebody out of
// their own email field is a worse outcome than making them ask again.
const RESEND_SECONDS = 60;

const hashCode = (code) =>
    crypto.createHash("sha256").update(String(code)).digest("hex");

exports.sendEmailCode = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");

    // ASKED BEFORE ANYTHING IS WRITTEN. A server with no mailbox configured
    // would otherwise store a code, promise to send it, and send nothing —
    // and the person would sit waiting for mail that was never going to
    // arrive. This is a 503: the request was fine, the server cannot do it.
    const why = mailer.unavailableReason();
    if (why) return fail(res, 503, why);

    try {
        const row = await pool.query(
            `SELECT email, email_verified_at FROM employees WHERE employee_id = $1`,
            [employeeId]);
        const email = row.rows[0]?.email;
        if (!email) {
            return fail(res, 400,
                "Save an email address first, then it can be verified.");
        }
        if (row.rows[0].email_verified_at) {
            return fail(res, 400, "That address is already verified.");
        }

        // THE AGE IS COMPUTED IN SQL, where the types are not in question.
        //
        // The JavaScript version of this was `new Date(row.sent_at + "Z")`,
        // which is correct here ONLY because config/db.js installs a type
        // parser that hands timestamps back as raw strings rather than Date
        // objects. Appending "Z" to "2026-08-14 11:13:00.12" gives UTC;
        // appending it to a Date — which is what pg does by default — gives
        // "…GMT+0530 (India Standard Time)Z", and an answer wrong by the
        // machine's offset.
        //
        // So the arithmetic was right, and right for a reason living in
        // another file that nothing here states. These columns are naive UTC
        // (see utils/ist_sql.js); comparing them in SQL needs no such
        // agreement, and cannot be broken by changing that parser later.
        const pending = await pool.query(
            `SELECT GREATEST(0, EXTRACT(EPOCH FROM
                        (NOW() AT TIME ZONE 'UTC') - sent_at))::int AS age
               FROM email_verifications WHERE employee_id = $1`,
            [employeeId]);
        if (pending.rows[0]) {
            const age = Number(pending.rows[0].age);
            // Throttled, because the button is otherwise a way to send
            // somebody a hundred messages — and mail providers stop
            // delivering for everybody when that happens.
            if (age < RESEND_SECONDS) {
                return fail(res, 429,
                    `A code was just sent. Ask again in ${RESEND_SECONDS - age} seconds.`);
            }
        }

        // crypto, not Math.random: this is a credential, however short-lived,
        // and Math.random is predictable from previous values.
        const code = String(crypto.randomInt(0, 10 ** CODE_LENGTH))
            .padStart(CODE_LENGTH, "0");

        // SENT BEFORE IT IS STORED. If the mail fails, nothing is written and
        // the previous code — if any — still works; storing first would
        // invalidate a code that is still in somebody's inbox in exchange for
        // one that never arrived.
        await mailer.send({
            to: email,
            subject: `${code} is your Amaze Connect verification code`,
            text: `Your Amaze Connect verification code is ${code}.\n\n`
                + `It is valid for ${CODE_VALID_MINUTES} minutes.\n\n`
                + `If you did not ask for this, somebody has typed your `
                + `address into their profile by mistake. You can ignore this `
                + `message — nothing has been given access to your account.`,
        });

        await pool.query(
            `INSERT INTO email_verifications
                 (employee_id, email, code_hash, expires_at, attempts, sent_at)
             VALUES ($1, $2, $3,
                     (NOW() AT TIME ZONE 'UTC') + INTERVAL '${CODE_VALID_MINUTES} minutes',
                     0, NOW() AT TIME ZONE 'UTC')
             ON CONFLICT (employee_id) DO UPDATE
                SET email = EXCLUDED.email, code_hash = EXCLUDED.code_hash,
                    expires_at = EXCLUDED.expires_at, attempts = 0,
                    sent_at = EXCLUDED.sent_at`,
            [employeeId, email, hashCode(code)]);

        // The address is echoed so the page can say where it went. The CODE
        // is not — it goes to the mailbox and nowhere else, which is the
        // entire point of the exercise.
        return res.json({ success: true, sent_to: email,
                          valid_minutes: CODE_VALID_MINUTES });
    } catch (error) {
        console.error("[MAIL]", req.originalUrl, error.message);
        // A rejected login, a wrong port, a provider refusing the message —
        // all of it is "we could not send it", and the person can act on
        // that. A 500 would tell the client the server is broken.
        return fail(res, 502,
            "The code could not be sent. Check the server's email settings.");
    }
};

exports.verifyEmailCode = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");

    const code = String(req.body?.code ?? "").trim();
    if (!/^[0-9]{6}$/.test(code)) {
        return fail(res, 400, "Enter the six-digit code from the email.");
    }

    try {
        const row = await pool.query(
            `SELECT v.email, v.code_hash, v.attempts,
                    v.expires_at < (NOW() AT TIME ZONE 'UTC') AS expired,
                    e.email AS current_email
               FROM email_verifications v
               JOIN employees e ON e.employee_id = v.employee_id
              WHERE v.employee_id = $1`, [employeeId]);
        const pending = row.rows[0];
        if (!pending) {
            return fail(res, 400, "Ask for a code first.");
        }
        if (pending.expired) {
            await pool.query(`DELETE FROM email_verifications WHERE employee_id = $1`,
                             [employeeId]);
            return fail(res, 400, "That code has expired. Ask for a new one.");
        }
        // The address changed after the code was sent. Verifying now would
        // prove the OLD address and mark the NEW one as proved — which is the
        // one way this whole exercise could be turned inside out.
        if (pending.email !== pending.current_email) {
            await pool.query(`DELETE FROM email_verifications WHERE employee_id = $1`,
                             [employeeId]);
            return fail(res, 400,
                "The address changed after that code was sent. Ask for a new one.");
        }
        if (pending.attempts >= MAX_ATTEMPTS) {
            return fail(res, 429,
                "Too many wrong codes. Ask for a new one.");
        }

        // timingSafeEqual over the hashes, so the comparison cannot be read
        // one character at a time. Both sides are the same length by
        // construction, which the function requires.
        const given = Buffer.from(hashCode(code));
        const stored = Buffer.from(String(pending.code_hash));
        const ok = given.length === stored.length
                && crypto.timingSafeEqual(given, stored);

        if (!ok) {
            await pool.query(
                `UPDATE email_verifications SET attempts = attempts + 1
                  WHERE employee_id = $1`, [employeeId]);
            const left = MAX_ATTEMPTS - (pending.attempts + 1);
            return fail(res, 400, left > 0
                ? `That code is not right. ${left} attempt${left === 1 ? "" : "s"} left.`
                : "That code is not right, and that was the last attempt. "
                  + "Ask for a new one.");
        }

        await pool.query(
            `UPDATE employees SET email_verified_at = NOW() AT TIME ZONE 'UTC'
              WHERE employee_id = $1`, [employeeId]);
        await pool.query(`DELETE FROM email_verifications WHERE employee_id = $1`,
                         [employeeId]);
        return res.json({ success: true, email: pending.email });
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

    // The filename is taken from the database, never from the URL, so there
    // is nothing here to traverse with whatever the caller sends.
    const wanted = String(req.params.employee_id || employeeId);

    try {
        // ANY SIGNED-IN EMPLOYEE MAY SEE ANY COLLEAGUE'S PHOTOGRAPH.
        //
        // The owner's decision, in those words: "1000 employee rahenge,
        // sabko ek dusre ka photo dikhna chahiye". This is a company
        // directory, and a face is the least sensitive thing in it — the
        // name, the employee id and the online state are already visible to
        // everybody.
        //
        // WHAT IT REPLACED, AND WHY THAT FAILED. The rule was "yourself,
        // administrators, or somebody you share a team with". A direct
        // message is a channel with two members and NO team, so two people
        // messaging each other saw initials — reported from use. Widening it
        // to channels would have fixed that one case and left the next
        // hundred: a new joiner in no team, a colleague from another
        // department, anybody in a list. Every one of those would have come
        // back as another bug report.
        //
        // STILL AUTHENTICATED-ONLY. `me(req)` above rejects anyone without a
        // valid session, so this is not public — it is the company, which is
        // what a staff directory is.

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
