/**
 * The attendance report, end to end.
 *
 * Absence is the one figure here that cannot be selected — it is the absence
 * of a row, derived by walking every date in the range. That makes it the
 * easiest number in the system to get quietly wrong, and the most damaging:
 * these totals are what payroll reads.
 *
 * The cases that matter most:
 *
 *   * a weekly off or a holiday is not an absence. Counting Sundays as
 *     absences would show every employee missing four days a month.
 *   * somebody who joined on the 20th was not absent on the 5th. Without
 *     that, every new hire opens their first report with a month of
 *     absences against their name.
 *   * average hours are averaged over days actually worked, not over the
 *     range, so approved leave does not read as a performance problem.
 *   * the super admin is excluded, exactly as they are from screenshots and
 *     idle tracking.
 *
 * Run:  node server/tests/test_reports.js
 */
const { execFileSync } = require("child_process");
const path = require("path");

const DB = `ets_reports_${process.pid}`;
const PORT = 8000 + ((process.pid + 613) % 1000);
const BASE = `http://127.0.0.1:${PORT}/api`;

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
        ...(body ? { body: JSON.stringify(body) } : {}),
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return { status: response.status, body: payload };
}

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`Attendance report, against a scratch database (${DB})\n`);

    psql("postgres", `CREATE DATABASE ${DB}`);
    try {
        for (const file of [
            path.join(root, "ets.sql"),
            path.join(root, "server", "migrations", "2026_08_05_password_management.sql"),
            path.join(root, "server", "migrations", "2026_08_05_work_calendar.sql"),
            path.join(root, "server", "migrations", "2026_08_05_late_grace.sql"),
            path.join(root, "server", "migrations", "2026_08_05_idle_daily.sql"),
        ]) {
            execFileSync("psql", ["-d", DB, "-v", "ON_ERROR_STOP=1", "-q", "-f", file],
                { stdio: "pipe" });
        }

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const seeded = await bcrypt.hash("SuperSecret123", 10);

        // Week of Mon 3 Aug 2026 to Sun 9 Aug 2026.
        // Sunday (ISO 7) is the weekly off, and 6 Aug is a holiday.
        psql(DB, `INSERT INTO employees (employee_id, username, password, role, created_at) VALUES
            ('SA001','superadmin','${seeded}','super_admin','2026-01-01'),
            ('E001','emp1','${seeded}','employee','2026-01-01'),
            ('E002','emp2','${seeded}','employee','2026-08-06'),
            ('E003','emp3','${seeded}','employee','2026-01-01')`);
        // ets.sql already seeds the single global row, so this updates it
        // rather than inserting a second one.
        psql(DB, `UPDATE employee_configs
                     SET shift_start='09:00', shift_end='18:00',
                         weekly_offs='7', late_grace_minutes=10
                   WHERE employee_id IS NULL`);
        psql(DB, `INSERT INTO holidays (holiday_date, name) VALUES ('2026-08-06','Test Holiday')`);

        // E001: works Mon, Tue (late), Fri. Absent Wed... but Thu 6th is the
        // holiday, so the only real absence is Wednesday 5th.
        //   03:30Z = 09:00 IST, 04:15Z = 09:45 IST
        psql(DB, `INSERT INTO attendance (employee_id, login_time, logout_time, total_hours) VALUES
            ('E001','2026-08-03 03:30:00','2026-08-03 12:30:00','9:00:00'),
            ('E001','2026-08-04 04:15:00','2026-08-04 12:30:00','8:15:00'),
            ('E001','2026-08-07 03:30:00','2026-08-07 11:30:00','8:00:00')`);

        // E002 joined on 6 Aug, worked the 7th. Days before joining must not
        // count as absences.
        psql(DB, `INSERT INTO attendance (employee_id, login_time, logout_time, total_hours) VALUES
            ('E002','2026-08-07 03:30:00','2026-08-07 12:30:00','9:00:00')`);

        // E003 never signed in at all.

        psql(DB, `INSERT INTO screenshots (employee_id, file_name, created_at) VALUES
            ('E001','a.enc','2026-08-03 05:00:00'),
            ('E001','b.enc','2026-08-04 05:00:00'),
            ('E001','c.enc','2026-08-04 06:00:00')`);

        process.env.DB_HOST = process.env.PGHOST || "127.0.0.1";
        process.env.DB_PORT = process.env.PGPORT || "5432";
        process.env.DB_NAME = DB;
        process.env.DB_USER = process.env.PGUSER || process.env.USER;
        process.env.DB_PASSWORD = process.env.PGPASSWORD || "unused-locally";
        process.env.JWT_SECRET = "test-secret-not-used-in-production";
        process.env.PORT = String(PORT);
        process.env.ENCRYPTION_KEY = "0".repeat(64);

        const { server, pool } = require(path.join(root, "server", "server.js"));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        let res = await api("POST", "/auth/login",
            { body: { username: "superadmin", password: "SuperSecret123" } });
        const token = res.body.token;

        const range = "from=2026-08-03&to=2026-08-09";
        res = await api("GET", `/admin/reports/attendance?${range}`, { token });
        check("the report responds", res.status === 200, `status ${res.status}`);
        check("the range is seven days", res.body.days === 7, String(res.body.days));

        const by = new Map((res.body.rows || []).map((r) => [r.employee_id, r]));

        check("the super admin is not in the report", !by.has("SA001"));
        check("all three employees are", by.size === 3, String(by.size));

        const e1 = by.get("E001");
        check("Sunday and the holiday are off days, not absences",
            e1?.off_days === 2, JSON.stringify(e1?.off_days));
        check("five working days in the week",
            e1?.working_days === 5, JSON.stringify(e1?.working_days));
        check("three days present", e1?.present_days === 3, JSON.stringify(e1?.present_days));
        check("two days absent", e1?.absent_days === 2, JSON.stringify(e1?.absent_days));
        check("the absent dates are named",
            JSON.stringify(e1?.absent_dates) === JSON.stringify(["2026-08-05", "2026-08-08"]),
            JSON.stringify(e1?.absent_dates));
        check("one late day", e1?.late_days === 1, JSON.stringify(e1?.late_days));
        check("45 late minutes", e1?.late_minutes === 45, JSON.stringify(e1?.late_minutes));
        check("hours are summed", e1?.total_hours === 25.25, JSON.stringify(e1?.total_hours));
        check("average is over days worked, not the range",
            e1?.avg_hours === 8.42, JSON.stringify(e1?.avg_hours));
        check("screenshots are counted", e1?.screenshots === 3, JSON.stringify(e1?.screenshots));

        const e2 = by.get("E002");
        check("days before joining are not absences",
            e2?.absent_days === 1, JSON.stringify({ a: e2?.absent_days, w: e2?.working_days }));
        check("only days from joining onward count as working",
            e2?.working_days === 2, JSON.stringify(e2?.working_days));

        const e3 = by.get("E003");
        check("somebody who never signed in is absent every working day",
            e3?.absent_days === 5 && e3?.present_days === 0,
            JSON.stringify({ a: e3?.absent_days, p: e3?.present_days }));
        check("and has no hours rather than a divide-by-zero",
            e3?.avg_hours === 0 && e3?.total_hours === 0,
            JSON.stringify({ avg: e3?.avg_hours, total: e3?.total_hours }));

        // ── idle time ───────────────────────────────────────────────────
        //
        // Reported by the client, one row per IST day. A day with no row is
        // "never reported", which is not the same as zero idle — the report
        // has to be able to tell the difference or it under-reports silently.
        check("no idle rows means nothing reported",
            e1?.idle_hours === 0 && e1?.idle_days_reported === 0,
            JSON.stringify({ h: e1?.idle_hours, d: e1?.idle_days_reported }));

        const empRes0 = await api("POST", "/auth/login",
            { body: { username: "emp1", password: "SuperSecret123" } });
        const e1Token = empRes0.body.token;

        res = await api("POST", "/logs/idle-daily",
            { token: e1Token, body: { day: "2026-08-03", idle_seconds: 3600 } });
        check("the client can report a day's idle time",
            res.status === 200, `status ${res.status}`);

        res = await api("POST", "/logs/idle-daily",
            { token: e1Token, body: { day: "2026-08-04", idle_seconds: 1800 } });

        res = await api("GET", `/admin/reports/attendance?${range}`, { token });
        const withIdle = (res.body.rows || []).find((r) => r.employee_id === "E001");
        check("idle hours are summed across days",
            withIdle?.idle_hours === 1.5, JSON.stringify(withIdle?.idle_hours));
        check("the report says how many days reported",
            withIdle?.idle_days_reported === 2,
            JSON.stringify(withIdle?.idle_days_reported));
        check("which is fewer than the days present, so partial",
            withIdle.idle_days_reported < withIdle.present_days,
            JSON.stringify({ r: withIdle.idle_days_reported, p: withIdle.present_days }));

        // A day grows while somebody is signed in, so it is re-sent.
        await api("POST", "/logs/idle-daily",
            { token: e1Token, body: { day: "2026-08-03", idle_seconds: 7200 } });
        res = await api("GET", `/admin/reports/attendance?${range}`, { token });
        const grown = (res.body.rows || []).find((r) => r.employee_id === "E001");
        check("re-sending a day replaces rather than adds",
            grown?.idle_hours === 2.5, JSON.stringify(grown?.idle_hours));

        // Two devices each hold their own running total; the smaller one
        // must not walk the figure backwards.
        await api("POST", "/logs/idle-daily",
            { token: e1Token, body: { day: "2026-08-03", idle_seconds: 60 } });
        res = await api("GET", `/admin/reports/attendance?${range}`, { token });
        const notLowered = (res.body.rows || []).find((r) => r.employee_id === "E001");
        check("a smaller total from another device cannot lower it",
            notLowered?.idle_hours === 2.5, JSON.stringify(notLowered?.idle_hours));

        res = await api("POST", "/logs/idle-daily",
            { token: e1Token, body: { day: "not-a-day", idle_seconds: 60 } });
        check("a malformed day is refused", res.status === 400, `status ${res.status}`);
        res = await api("POST", "/logs/idle-daily",
            { token: e1Token, body: { day: "2026-08-03", idle_seconds: 999999 } });
        check("more idle than a day contains is refused",
            res.status === 400, `status ${res.status}`);
        res = await api("POST", "/logs/idle-daily",
            { body: { day: "2026-08-03", idle_seconds: 60 } });
        check("reporting idle time needs a token", res.status === 401, `status ${res.status}`);

        // ── filters and guards ──────────────────────────────────────────
        res = await api("GET", `/admin/reports/attendance?${range}&employee_id=E001`, { token });
        check("a single employee can be requested",
            res.body.rows?.length === 1 && res.body.rows[0].employee_id === "E001",
            JSON.stringify(res.body.rows?.length));

        res = await api("GET", "/admin/reports/attendance?from=2026-08-09&to=2026-08-03", { token });
        check("a backwards range is refused", res.status === 400, `status ${res.status}`);

        res = await api("GET", "/admin/reports/attendance?from=2020-01-01&to=2026-08-09", { token });
        check("an oversized range is refused", res.status === 400, `status ${res.status}`);

        res = await api("GET", "/admin/reports/attendance?from=nope&to=2026-08-09", { token });
        check("a malformed date is refused", res.status === 400, `status ${res.status}`);

        res = await api("POST", "/auth/login",
            { body: { username: "emp1", password: "SuperSecret123" } });
        res = await api("GET", `/admin/reports/attendance?${range}`, { token: res.body.token });
        check("an employee cannot read the report", res.status === 403, `status ${res.status}`);

        server.close();
        await pool.end();

    } finally {
        try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.exit(1);
    }
    console.log("all report checks passed");
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
