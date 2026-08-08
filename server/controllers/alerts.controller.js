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
exports.getAlerts = async (req, res) => {
    try {
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
               LEFT JOIN employee_configs g ON g.employee_id = 'global'
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
        const seen = await pool.query(
            `SELECT e.employee_id,
                    -- AT TIME ZONE 'UTC' here CONVERTS, it does not
                    -- relabel. active_sessions is the only table here whose
                    -- timestamps carry a zone; the other two are naive UTC.
                    -- Mixing them lets Postgres coerce using whatever the
                    -- session timezone happens to be — correct today only
                    -- because config/db.js pins it to UTC, and wrong by five
                    -- and a half hours the moment anything else runs this.
                    MAX(GREATEST(s.last_seen AT TIME ZONE 'UTC',
                                 a.login_time, sh.created_at)) IS NULL
                        AS never_seen,
                    EXTRACT(EPOCH FROM (
                        (NOW() AT TIME ZONE 'UTC')
                        - MAX(GREATEST(s.last_seen AT TIME ZONE 'UTC',
                                       a.login_time, sh.created_at))
                    )) / 60 AS quiet_minutes
               FROM employees e
               LEFT JOIN active_sessions s ON s.employee_id = e.employee_id
               LEFT JOIN attendance a      ON a.employee_id = e.employee_id
               LEFT JOIN screenshots sh    ON sh.employee_id = e.employee_id
              WHERE e.role = 'employee'
              GROUP BY e.employee_id`
        );
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

        const clock = await pool.query(
            `SELECT ${istToday()} AS today,
                    TO_CHAR(NOW() AT TIME ZONE 'Asia/Kolkata', 'HH24:MI') AS now_ist`);
        const isoDate = String(clock.rows[0].today).slice(0, 10);
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

        return res.json({
            success: true,
            generated_at: new Date().toISOString(),
            enabled: rules.setting(settings, "alerts_enabled"),
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
