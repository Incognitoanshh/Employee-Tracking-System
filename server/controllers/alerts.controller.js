/**
 * What is wrong right now, for the admin panel to show.
 *
 * This file gathers facts; alert_rules.js decides what is worth saying about
 * them. The split is deliberate — the decisions are the part worth testing,
 * and they should be testable against an invented Tuesday rather than a real
 * one.
 *
 * ONE QUERY PER FACT, NOT PER EMPLOYEE. Everything below is fetched for the
 * whole company at once and joined in memory. Asking four questions per
 * employee is four hundred round trips at a hundred people, which on this
 * connection is a page that never finishes loading.
 *
 * NOTHING IS WRITTEN. No table, no scheduler, no purge. See the note at the
 * top of alert_rules for why alerts are computed on read rather than stored.
 */

const pool = require("../config/db");
const rules = require("../utils/alert_rules");
const { istDate, istToday } = require("../utils/ist_sql");

const fail = (res, status, message) => res.status(status).json({ success: false, message });

function serverError(res, req, err) {
    console.error("[500]", req.method, req.originalUrl, err.message);
    return res.status(500).json({ success: false, message: "Internal server error" });
}

/** Every alert-related setting, with the saved value where there is one. */
async function loadSettings() {
    const keys = Object.keys(rules.DEFAULTS);
    const saved = await pool.query(
        `SELECT key, value FROM app_settings WHERE key = ANY($1)`, [keys]);
    const settings = {};
    for (const row of saved.rows) settings[row.key] = row.value;
    return settings;
}

/**
 * GET /api/admin/alerts
 *
 * Returns every current alert, worst first, plus the counts the bell needs.
 */
/**
 * Work out every alert that is true right now.
 *
 * PULLED OUT OF getAlerts SO THE EMAILER SEES THE SAME LIST. It was inline,
 * and a second caller would have meant a second copy of "who is late" — which
 * is the shape of bug where a page and an email disagree about the same
 * morning, and nobody can say which is right.
 *
 * Nothing is stored: the alerts are derived from attendance, activity and the
 * calendar each time. utils/alert_mailer.js keeps its own record of what it
 * has already sent, which is a different question.
 */
async function collectAlerts() {
    const settings = await loadSettings();

    // Only employees. An administrator who has not logged in is not an
    // attendance problem, and their own missing shift would sit at the
    // top of their own alert list every morning.
    const people = await pool.query(
        `SELECT e.employee_id, e.username, e.full_name, e.suspended,
                COALESCE(c.shift_start, g.shift_start)             AS shift_start,
                COALESCE(c.late_grace_minutes, g.late_grace_minutes) AS late_grace_minutes,
                COALESCE(c.weekly_offs, g.weekly_offs)             AS weekly_offs
           FROM employees e
           LEFT JOIN employee_configs c ON c.employee_id = e.employee_id
           -- employee_id IS NULL, not 'global'.
           --
           -- BUG this fixes: this file invented its own convention. The
           -- global configuration row is stored with a NULL employee_id
           -- everywhere else — config.controller, attendance.controller,
           -- auth.controller all read it that way — so this join matched
           -- nothing in production.
           --
           -- The effect was quiet and complete: with no global shift,
           -- "has not logged in" could never fire for anybody without a
           -- per-employee config, and with no global weekly offs, idle
           -- alerts fired on Sundays and holidays. Both are the exact
           -- failures the rules were written to avoid.
           --
           -- The test agreed with the code because I wrote both from the
           -- same wrong assumption — it seeded a row called 'global'. It
           -- now seeds what production actually has.
           LEFT JOIN employee_configs g ON g.employee_id IS NULL
          WHERE e.role = 'employee'
          ORDER BY e.employee_id`
    );

    // Minutes since the most recent sign of life, of ANY kind. Three
    // sources because each can be absent for an innocent reason: a
    // heartbeat stops at logout, a login happens once a day, and
    // screenshots pause when the shift ends. The most recent of the three
    // is the only honest answer to "is this app still running".
    // The AGE IS COMPUTED IN SQL, not in JavaScript, and deliberately.
    // These columns are TIMESTAMP WITHOUT TIME ZONE holding UTC; the
    // driver hands them over as a value JavaScript will happily interpret
    // in the server's own timezone, which is how a five-and-a-half-hour
    // error gets in. Postgres already knows what the column means.
    // THREE SUBQUERIES, NOT THREE JOINS, and the difference is not small.
    //
    // Joining employees to active_sessions, attendance and screenshots and
    // then grouping builds the cartesian product of each person's rows
    // before collapsing it. Measured at a thousand employees with 30
    // attendance rows and 50 screenshots each: 1,500,000 intermediate rows
    // to produce 1,000 answers, and it grows with the PRODUCT of the two
    // histories, so a year of use makes it far worse rather than a little.
    // The subquery form does the same work in 24 ms.
    //
    // AT TIME ZONE 'UTC' on last_seen CONVERTS it: active_sessions is the
    // only table here whose timestamps carry a zone, and the other two are
    // naive UTC, so they must be made comparable explicitly rather than
    // left to whatever the session timezone happens to be.
    const seen = await pool.query(
        `SELECT e.employee_id,
                GREATEST(
                    (SELECT MAX(s.last_seen AT TIME ZONE 'UTC')
                       FROM active_sessions s WHERE s.employee_id = e.employee_id),
                    (SELECT MAX(a.login_time)
                       FROM attendance a WHERE a.employee_id = e.employee_id),
                    (SELECT MAX(sh.created_at)
                       FROM screenshots sh WHERE sh.employee_id = e.employee_id)
                ) IS NULL AS never_seen,
                EXTRACT(EPOCH FROM (
                    (NOW() AT TIME ZONE 'UTC')
                    - GREATEST(
                        (SELECT MAX(s.last_seen AT TIME ZONE 'UTC')
                           FROM active_sessions s WHERE s.employee_id = e.employee_id),
                        (SELECT MAX(a.login_time)
                           FROM attendance a WHERE a.employee_id = e.employee_id),
                        (SELECT MAX(sh.created_at)
                           FROM screenshots sh WHERE sh.employee_id = e.employee_id))
                )) / 60 AS quiet_minutes
           FROM employees e
          WHERE e.role = 'employee'`
    );
    // The AGE IS COMPUTED IN SQL. The driver hands a naive timestamp to
    // JavaScript as a Date interpreted in this process's own timezone, so
    // doing the subtraction here reintroduces exactly the five-and-a-half
    // hour error the rest of this file is careful about. Postgres already
    // knows what the column means.
    const lastSeen = new Map();
    for (const row of seen.rows) {
        lastSeen.set(row.employee_id,
            row.never_seen ? null : Math.max(0, Math.round(Number(row.quiet_minutes))));
    }

    const loggedIn = await pool.query(
        `SELECT DISTINCT employee_id FROM attendance
          WHERE ${istDate("login_time")} = ${istToday()}`
    );
    const loggedInToday = new Set(loggedIn.rows.map((r) => r.employee_id));

    const idle = await pool.query(
        `SELECT employee_id, idle_seconds FROM idle_daily
          WHERE day = ${istToday()}`
    );
    const idleMinutes = new Map(
        idle.rows.map((r) => [r.employee_id, Math.round(Number(r.idle_seconds || 0) / 60)]));

    const days = await pool.query(
        `SELECT TO_CHAR(holiday_date, 'YYYY-MM-DD') AS day FROM holidays`);
    const holidays = new Set(days.rows.map((r) => r.day));

    // FORMATTED IN SQL, as text.
    //
    // BUG this fixes: this asked for a DATE and did
    // String(row.today).slice(0, 10). The driver hands a DATE over as a
    // JavaScript Date, so that produced "Sun Aug 09" — not an ISO date.
    // isNonWorkingDay parses `${isoDate}T00:00:00Z`, which was then
    // Invalid Date, so getUTCDay() was NaN and the weekly-off test
    // silently answered "no" for every day of the year.
    //
    // The effect: idle alerts fired on Sundays and holidays, and "has not
    // logged in" would have chased people on their day off. Both are the
    // exact failures the rules were written to avoid, and the unit tests
    // could not see it because they pass an ISO string in directly.
    const clock = await pool.query(
        `SELECT TO_CHAR(NOW() AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD') AS today,
                TO_CHAR(NOW() AT TIME ZONE 'Asia/Kolkata', 'HH24:MI') AS now_ist`);
    const isoDate = String(clock.rows[0].today);
    const [hh, mm] = String(clock.rows[0].now_ist).split(":").map(Number);
    const nowMinutes = hh * 60 + mm;

    const alerts = [];
    for (const employee of people.rows) {
        alerts.push(...rules.forEmployee({
            employee,
            settings,
            holidays,
            isoDate,
            nowMinutes,
            lastSeenMinutes: lastSeen.has(employee.employee_id)
                ? lastSeen.get(employee.employee_id) : null,
            loggedInToday: loggedInToday.has(employee.employee_id),
            idleMinutes: idleMinutes.get(employee.employee_id) || 0,
        }));
    }

    const counts = { HIGH: 0, MEDIUM: 0, LOW: 0 };
    for (const alert of alerts) counts[alert.severity] += 1;

    return {
        settings,
        enabled: rules.setting(settings, "alerts_enabled"),
        alerts,
        counts,
    };
}

exports.collectAlerts = collectAlerts;

exports.getAlerts = async (req, res) => {
    try {
        const { enabled, alerts, counts } = await collectAlerts();
        return res.json({
            success: true,
            generated_at: new Date().toISOString(),
            enabled,
            total: alerts.length,
            counts,
            alerts,
        });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/**
 * GET /api/admin/alerts/settings — the thresholds, so Configuration can show
 * what is actually in force rather than what it assumes.
 */
exports.getSettings = async (req, res) => {
    try {
        const saved = await loadSettings();
        const current = {};
        for (const key of Object.keys(rules.DEFAULTS)) {
            current[key] = rules.setting(saved, key);
        }
        return res.json({ success: true, settings: current, defaults: rules.DEFAULTS });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/**
 * POST /api/admin/alerts/settings
 *
 * Only keys this feature owns are accepted, and only sane values. A settings
 * endpoint that writes whatever it is handed is a way to set any row in
 * app_settings from the admin panel, including the retention keys.
 */
exports.saveSettings = async (req, res) => {
    const body = req.body || {};
    const updates = [];
    for (const [key, fallback] of Object.entries(rules.DEFAULTS)) {
        if (!(key in body)) continue;
        const raw = body[key];
        if (typeof fallback === "boolean") {
            updates.push([key, raw === true || raw === "true" ? "true" : "false"]);
            continue;
        }
        const number = Number(raw);
        if (!Number.isFinite(number) || number < 0) {
            return fail(res, 400, `${key} must be a number of minutes or hours, not negative.`);
        }
        // An upper bound as well as a lower one. A threshold of a million
        // minutes disables the alert while looking like it is switched on,
        // which is worse than turning it off honestly.
        if (number > 100000) return fail(res, 400, `${key} is too large to be meaningful.`);
        updates.push([key, String(Math.round(number))]);
    }
    if (updates.length === 0) return fail(res, 400, "Nothing to save");

    const who = req.employee?.employee_id || null;
    try {
        for (const [key, value] of updates) {
            await pool.query(
                `INSERT INTO app_settings (key, value, updated_at, updated_by)
                 VALUES ($1, $2, NOW(), $3)
                 ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at,
                        updated_by = EXCLUDED.updated_by`,
                [key, value, who]
            );
        }
        return res.json({ success: true, saved: updates.length });
    } catch (err) {
        return serverError(res, req, err);
    }
};


// ────────────────────────────────────────────────────── alerts by email

const alertMailer = require("../utils/alert_mailer");

/**
 * GET /api/admin/alerts/email — who alerts go to, and what has been sent.
 *
 * The delivery list is the answer to "I never got the alert": it says whether
 * one was sent, refused, or never generated, with the reason the mail server
 * gave.
 */
exports.getEmailSettings = async (req, res) => {
    try {
        const config = await alertMailer.loadMailSettings();
        const recent = await pool.query(
            `SELECT id, employee_id, alert_type, ist_day, severity, subject,
                    recipients, status, attempts, error, sent_at
               FROM alert_emails
              ORDER BY sent_at DESC
              LIMIT 50`);
        const counts = await pool.query(
            `SELECT status, COUNT(*)::int AS n FROM alert_emails
              WHERE sent_at > (NOW() AT TIME ZONE 'UTC') - INTERVAL '30 days'
              GROUP BY status`);

        return res.json({
            success: true,
            // NEVER THE PASSWORD, and not even the user — this endpoint says
            // whether sending is possible, not how it is done. The settings
            // live in .env on the server and have no business travelling to
            // an admin panel.
            can_send: alertMailer === null ? false : require("../utils/mailer").isConfigured(),
            unavailable_reason: require("../utils/mailer").unavailableReason(),
            recipients: config.recipients,
            immediate: [...config.immediate],
            digest: config.digest,
            digest_hour: config.digestHour,
            counts: Object.fromEntries(counts.rows.map((r) => [r.status, r.n])),
            recent: recent.rows,
        });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/**
 * POST /api/admin/alerts/email — set the recipients and what is immediate.
 *
 * Super admin only. An alert list is who finds out that somebody did not turn
 * up, which is the owner's business rather than a working setting.
 */
exports.saveEmailSettings = async (req, res) => {
    const body = req.body || {};

    // THE SHAPE OF AN ADDRESS IS CHECKED, and nothing else can be. Whether it
    // works is answered by sending to it, which is what the test button does.
    const recipients = String(body.recipients ?? "")
        .split(/[,;\s]+/).map((a) => a.trim()).filter(Boolean);
    for (const address of recipients) {
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(address)) {
            return fail(res, 400, `"${address}" does not look like an email address.`);
        }
    }
    if (recipients.length > 20) {
        return fail(res, 400, "Twenty addresses is already more than anybody reads.");
    }

    const KNOWN = ["NOT_REPORTING", "NEVER_REPORTED", "NO_LOGIN", "HIGH_IDLE"];
    let immediate = body.immediate;
    if (immediate !== undefined) {
        immediate = Array.isArray(immediate) ? immediate
                  : String(immediate).split(",").map((t) => t.trim()).filter(Boolean);
        const unknown = immediate.filter((t) => !KNOWN.includes(t));
        if (unknown.length) {
            return fail(res, 400, `Not an alert type: ${unknown.join(", ")}`);
        }
    }

    const hour = body.digest_hour;
    if (hour !== undefined && (!Number.isInteger(Number(hour))
                               || Number(hour) < 0 || Number(hour) > 23)) {
        return fail(res, 400, "The summary hour is 0 to 23, in IST.");
    }

    const writes = [["alert_email_to", recipients.join(", ")]];
    if (immediate !== undefined) writes.push(["alert_email_immediate", immediate.join(",")]);
    if (body.digest !== undefined) {
        writes.push(["alert_email_digest", body.digest === true || body.digest === "true"
                                           ? "true" : "false"]);
    }
    if (hour !== undefined) writes.push(["alert_email_digest_hour", String(Number(hour))]);

    try {
        for (const [key, value] of writes) {
            await pool.query(
                `INSERT INTO app_settings (key, value, updated_at, updated_by)
                 VALUES ($1, $2, NOW() AT TIME ZONE 'UTC', $3)
                 ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at,
                        updated_by = EXCLUDED.updated_by`,
                [key, value, req.employee?.employee_id || null]);
        }
        return res.json({ success: true, recipients });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/**
 * POST /api/admin/alerts/email/run — send now, rather than waiting for the
 * timer.
 *
 * For the person who has just filled the settings in and wants to know
 * whether they work. It obeys the same "already sent today" rule, so pressing
 * it twice does not send anything twice.
 */
exports.runEmailsNow = async (req, res) => {
    try {
        const result = await alertMailer.runAlertEmails();
        return res.json({ success: true, ...result });
    } catch (err) {
        return serverError(res, req, err);
    }
};
