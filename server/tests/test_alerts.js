/**
 * The alerts endpoint, against a real database.
 *
 * alert_rules is tested on its own; this checks the half that talks to
 * Postgres, where the mistakes are of a different kind:
 *
 *   * "Last seen" must be the most recent of THREE sources. A heartbeat stops
 *     at logout, a login happens once a day, and screenshots pause when the
 *     shift ends — so any single source reports a working app as silent.
 *   * Timestamps are naive UTC and the shift is IST. Comparing them without
 *     converting is the bug that once made the dashboard read zero for five
 *     and a half hours every day.
 *   * The settings endpoint must not be a way to write any row in
 *     app_settings from the admin panel.
 *
 * Run:  node server/tests/test_alerts.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_alerts_${process.pid}`;
const PORT = 8000 + ((process.pid + 733) % 1000);
const BASE = `http://127.0.0.1:${PORT}/api`;
const PASSWORD = "SuperSecret123";

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}

function psql(db, sql) {
    return execFileSync("psql", ["-d", db, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        { encoding: "utf8" }).trim();
}

async function api(method, route, { token, body } = {}) {
    const response = await fetch(`${BASE}${route}`, {
        method,
        headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        ...(body && method !== "GET" ? { body: JSON.stringify(body) } : {}),
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return { status: response.status, body: payload };
}

const login = async (u) =>
    (await api("POST", "/auth/login", { body: { username: u, password: PASSWORD } })).body.token;

const find = (list, id, type) =>
    (list || []).find((a) => a.employee_id === id && a.type === type);

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`Alerts (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name) VALUES
            ('A001','admin1','${hash}','admin','Priya Nair'),
            ('E001','rajesh','${hash}','employee','Rajesh Kumar'),
            ('E002','amit','${hash}','employee','Amit Sharma'),
            ('E003','sneha','${hash}','employee','Sneha Iyer'),
            ('E004','vikram','${hash}','employee','Vikram Rao')`);

        // Everybody works 09:00 with ten minutes' grace, Sunday off.
        // employee_id NULL is how the global row is stored — see
        // config.controller. Seeding a row literally named 'global' is what
        // let a broken join pass for an afternoon.
        // UPDATE, not INSERT. A migration already creates the one global row,
        // and a unique constraint keeps it the only one — which is itself the
        // proof that NULL is the convention.
        psql(DB, `UPDATE employee_configs
                     SET shift_start='09:00', shift_end='18:00',
                         late_grace_minutes=10, weekly_offs='7'
                   WHERE employee_id IS NULL`);

        Object.assign(process.env, {
            DB_HOST: process.env.PGHOST || "127.0.0.1",
            DB_PORT: process.env.PGPORT || "5432",
            DB_NAME: DB,
            DB_USER: process.env.PGUSER || process.env.USER,
            DB_PASSWORD: process.env.PGPASSWORD || "unused-locally",
            JWT_SECRET: "test-secret-not-used-in-production",
            PORT: String(PORT),
            ENCRYPTION_KEY: "0".repeat(64),
        });

        const { server, pool } = require(path.join(root, "server", "server.js"));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        const admin = await login("admin1");
        const worker = await login("rajesh");

        console.log("Who may ask");
        let res = await api("GET", "/admin/alerts");
        check("without a token, nothing", res.status === 401, `status ${res.status}`);
        res = await api("GET", "/admin/alerts", { token: worker });
        check("an employee cannot read the alert list about themselves and everybody else",
            res.status === 403, `status ${res.status}`);

        console.log("\nWhat counts as a sign of life");
        // Rajesh logged in just now, so his session heartbeat is fresh.
        // Amit has no session but a screenshot minutes ago — a real state,
        // and the one a single-source query gets wrong.
        psql(DB, `INSERT INTO screenshots (employee_id, file_name, created_at)
                  VALUES ('E002','x.enc', (NOW() AT TIME ZONE 'UTC') - INTERVAL '10 minutes')`);
        // Sneha last did anything three days ago: attendance only.
        psql(DB, `INSERT INTO attendance (employee_id, login_time, logout_time)
                  VALUES ('E003', (NOW() AT TIME ZONE 'UTC') - INTERVAL '3 days',
                                  (NOW() AT TIME ZONE 'UTC') - INTERVAL '3 days' + INTERVAL '8 hours')`);
        // Vikram has never done anything at all.

        res = await api("GET", "/admin/alerts", { token: admin });
        check("an admin may", res.status === 200, `status ${res.status}`);
        let list = res.body.alerts;

        check("a fresh heartbeat is not reported as silence",
            !find(list, "E001", "NOT_REPORTING"), JSON.stringify(list.map((a) => a.type)));
        check("a recent SCREENSHOT counts as alive even with no session",
            !find(list, "E002", "NOT_REPORTING"),
            "a working app was reported as dead");
        check("three days of nothing is reported",
            Boolean(find(list, "E003", "NOT_REPORTING")),
            JSON.stringify(list.filter((a) => a.employee_id === "E003")));
        check("and it says how long", /3 d/.test(find(list, "E003", "NOT_REPORTING").title),
            find(list, "E003", "NOT_REPORTING").title);
        check("an account that has never reported is called out",
            Boolean(find(list, "E004", "NEVER_REPORTED")),
            JSON.stringify(list.filter((a) => a.employee_id === "E004")));

        check("the counts match the list",
            res.body.total === list.length
            && res.body.counts.HIGH + res.body.counts.MEDIUM + res.body.counts.LOW === list.length,
            JSON.stringify(res.body.counts));

        console.log("\nAdministrators are not employees");
        check("an admin is never in their own alert list",
            !list.some((a) => a.employee_id === "A001"),
            JSON.stringify(list.map((a) => a.employee_id)));

        console.log("\nSuspending somebody silences them");
        psql(DB, `UPDATE employees SET suspended = TRUE WHERE employee_id = 'E004'`);
        res = await api("GET", "/admin/alerts", { token: admin });
        check("a suspended account raises nothing",
            !res.body.alerts.some((a) => a.employee_id === "E004"),
            JSON.stringify(res.body.alerts.map((a) => a.employee_id)));
        psql(DB, `UPDATE employees SET suspended = FALSE WHERE employee_id = 'E004'`);

        console.log("\nIdle, counted in IST rather than UTC");
        // Written against the IST day, which is what idle_daily stores. If
        // the endpoint compared against the UTC date instead, this row would
        // be invisible for five and a half hours out of every twenty-four.
        psql(DB, `INSERT INTO idle_daily (employee_id, day, idle_seconds)
                  VALUES ('E001', DATE(NOW() AT TIME ZONE 'Asia/Kolkata'), 4 * 3600)`);
        res = await api("GET", "/admin/alerts", { token: admin });
        // ── a day off, whatever day it actually is ──────────────────────
        //
        // Making today a holiday tests the rule on every day of the week
        // rather than one in seven. It caught a real bug the Sunday-only
        // version could not: the controller passed the date to the rules as
        // "Sun Aug 09" rather than "2026-08-09", so isNonWorkingDay parsed an
        // Invalid Date and answered "working day" for every day of the year.
        // Idle alerts fired on holidays and Sundays for as long as that stood.
        psql(DB, `INSERT INTO holidays (holiday_date, name)
                  VALUES (DATE(NOW() AT TIME ZONE 'Asia/Kolkata'), 'Test holiday')
                  ON CONFLICT DO NOTHING`);
        res = await api("GET", "/admin/alerts", { token: admin });
        check("on a holiday, idle raises nothing",
            !find(res.body.alerts, "E001", "HIGH_IDLE"),
            JSON.stringify(find(res.body.alerts, "E001", "HIGH_IDLE")));
        check("and nobody is chased for not logging in either",
            !res.body.alerts.some((a) => a.type === "NO_LOGIN"),
            JSON.stringify(res.body.alerts.map((a) => a.type)));
        psql(DB, `DELETE FROM holidays`);

        res = await api("GET", "/admin/alerts", { token: admin });
        const idle = find(res.body.alerts, "E001", "HIGH_IDLE");
        const sunday = psql(DB,
            `SELECT EXTRACT(ISODOW FROM (NOW() AT TIME ZONE 'Asia/Kolkata'))::int`) === "7";
        if (sunday) {
            check("today is Sunday, so idle raises nothing — the day off rule wins",
                !idle, JSON.stringify(idle));
        } else {
            check("four idle hours is reported", Boolean(idle), JSON.stringify(idle));
            check("and quietly — idle is a reason to look, not to act",
                idle.severity === "LOW", idle && idle.severity);
        }

        console.log("\nThresholds live in settings");
        res = await api("GET", "/admin/alerts/settings", { token: admin });
        check("the panel can read what is actually in force",
            res.status === 200 && res.body.settings.alert_silent_hours === 24,
            JSON.stringify(res.body.settings));

        res = await api("POST", "/admin/alerts/settings",
            { token: admin, body: { alert_silent_hours: 96 } });
        check("and change it", res.status === 200, `status ${res.status}`);

        res = await api("GET", "/admin/alerts", { token: admin });
        check("raising the silence threshold really silences the alert",
            !find(res.body.alerts, "E003", "NOT_REPORTING"),
            "the setting was read but not used");

        res = await api("POST", "/admin/alerts/settings",
            { token: admin, body: { alert_silent_hours: -5 } });
        check("a negative threshold is refused", res.status === 400, `status ${res.status}`);
        res = await api("POST", "/admin/alerts/settings",
            { token: admin, body: { alert_silent_hours: 99999999 } });
        check("so is one so large it disables the alert while looking switched on",
            res.status === 400, `status ${res.status}`);

        // The one that matters: this endpoint must not be a general way to
        // write app_settings from the admin panel.
        const before = psql(DB,
            `SELECT value FROM app_settings WHERE key = 'screenshot_retention_days'`);
        res = await api("POST", "/admin/alerts/settings",
            { token: admin, body: { screenshot_retention_days: "1" } });
        const after = psql(DB,
            `SELECT value FROM app_settings WHERE key = 'screenshot_retention_days'`);
        check("an unrelated setting cannot be written through this route",
            after === before, `retention went from ${before} to ${after}`);

        console.log("\nThe master switch");
        await api("POST", "/admin/alerts/settings",
            { token: admin, body: { alerts_enabled: false } });
        res = await api("GET", "/admin/alerts", { token: admin });
        check("everything can be turned off",
            res.body.total === 0 && res.body.enabled === false,
            JSON.stringify({ total: res.body.total, enabled: res.body.enabled }));

        server.close();
        await pool.end();
    } finally {
        try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.stdout.write("", () => process.exit(1));
    } else {
        console.log("all alerts checks passed");
        process.stdout.write("", () => process.exit(0));
    }
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
