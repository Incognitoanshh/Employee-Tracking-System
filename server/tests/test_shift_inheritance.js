/**
 * An empty shift on an employee means "use the global one", never "no shift".
 *
 * THE BUG THIS EXISTS FOR, reported from the admin panel on 21 August 2026:
 * "abhi kis baat ka extra session, 9-18 hai na". The shift WAS 09:00-18:00,
 * set globally, and the Attendance table said "No Shift Set" on every row.
 *
 * How it happened, and why nobody touched a shift to cause it. Saving any
 * per-employee setting — a screenshot count, a logging toggle — creates that
 * employee's own row in employee_configs, and the INSERT that creates it
 * writes NULL into shift_start and shift_end instead of leaving the column
 * defaults alone. The row is not saying "this person has no shift". It is
 * saying nothing about the shift at all.
 *
 * Three readers then had to decide what that NULL meant, and they disagreed:
 *
 *   * config.controller, which the employee's own app syncs from, fell back
 *     to the global row field by field — so the app kept working 09:00-18:00,
 *     correctly, the whole time;
 *   * alerts.controller COALESCEd to the global row, so lateness alerts also
 *     kept working;
 *   * attendance.controller and the two admin reads took the employee's row
 *     WHOLE, so the silent NULL hid the global shift and the column emptied.
 *
 * The cost was not cosmetic. With no shift to be measured against, nobody
 * could be late: every arrival read "No Shift Set", which is exactly what a
 * genuinely unconfigured system looks like.
 *
 * WHAT THIS TEST REFUSES TO ACCEPT. That the fix works because one screen
 * looks right. The same shift is read in four places here, and the test fails
 * if any two of them disagree — because two of them disagreeing is the bug.
 *
 * Run:  node server/tests/test_shift_inheritance.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_shiftinherit_${process.pid}`;
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
    console.log("\nThe shift an employee has not overridden\n");

    migrate(DB);

    const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
    const seeded = await bcrypt.hash("SuperSecret123", 10);
    psql(DB, `INSERT INTO employees (employee_id, username, password, role)
              VALUES ('SA001','superadmin','${seeded}','super_admin'),
                     ('AD001','raju','${seeded}','admin')`);

    // The shift everybody works, set once, globally. Nothing per-employee.
    psql(DB, `UPDATE employee_configs
                 SET shift_start='09:00', shift_end='18:00',
                     weekly_offs='7', late_grace_minutes=10
               WHERE employee_id IS NULL`);

    // Two sign-ins on the same day. Stored UTC; IST is +5:30, so 05:47Z is
    // 11:17 IST — two hours and change after a 09:00 start.
    psql(DB, `INSERT INTO attendance (employee_id, login_time, logout_time) VALUES
        ('AD001', '2026-08-21 05:47:00', '2026-08-21 06:06:00'),
        ('AD001', '2026-08-21 06:12:00', NULL)`);

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

    const login = await api("POST", "/auth/login",
        { body: { username: "superadmin", password: "SuperSecret123" } });
    const token = login.body.token;

    const lateRow = async () => {
        const res = await api("GET", "/attendance/all?employee_id=AD001", { token });
        return (res.body.data || []).find(
            (r) => String(r.login_time).slice(11, 16) === "05:47");
    };

    // ── before: no per-employee row at all ──────────────────────────────
    const before = await lateRow();
    check("with no per-employee row, an 11:17 arrival is late",
        before?.shift_label === "Late 2h 17m", JSON.stringify(before?.shift_label));

    // ── THE MOMENT THE BUG WAS CREATED ──────────────────────────────────
    // An admin changes this person's screenshot count. The form has no shift
    // field on it, so the request says nothing about the shift — and that is
    // the whole point: no human decided anything about a shift here.
    const saved = await api("POST", "/admin/config", {
        token,
        body: {
            employee_id: "AD001",
            screenshot_min_minutes: 3, screenshot_max_minutes: 10,
            screenshots_per_day: 12, upload_interval_minutes: 60,
            idle_threshold_seconds: 60, force_logout: false,
            verbose_logging: false, late_grace_minutes: 10,
        },
    });
    check("saving an unrelated setting succeeds", saved.status === 200,
        `${saved.status} ${saved.body.message || ""}`);

    // The row really does record nothing about the shift — if this stops
    // being true the rest of the test proves nothing, so it is asserted
    // rather than assumed.
    const stored = psql(DB, `SELECT COALESCE(shift_start::text,'NULL')
                               FROM employee_configs WHERE employee_id='AD001'`);
    check("and the row it created says nothing about the shift",
        stored === "NULL", `stored ${stored}`);

    // ── after: every reader must still see 09:00-18:00 ──────────────────
    const after = await lateRow();
    check("the attendance table still measures against the global shift",
        after?.shift_label === "Late 2h 17m", JSON.stringify(after?.shift_label));
    check("and still shows the window it measured against",
        after?.shift_window === "09:00–18:00", JSON.stringify(after?.shift_window));

    const second = (await api("GET", "/attendance/all?employee_id=AD001", { token }))
        .body.data.find((r) => String(r.login_time).slice(11, 16) === "06:12");
    // A second sign-in is still not judged for lateness — that part was
    // always right, and the fix must not turn "Extra Session" into a verdict.
    check("a second sign-in the same day is still not judged",
        second?.shift_label === "Extra Session", JSON.stringify(second?.shift_label));
    check("but it too knows which shift the day belonged to",
        second?.shift_window === "09:00–18:00", JSON.stringify(second?.shift_window));

    const config = await api("GET", "/admin/config/AD001", { token });
    check("the config form shows the shift instead of two empty boxes",
        String(config.body.config?.shift_start).startsWith("09:00")
        && String(config.body.config?.shift_end).startsWith("18:00"),
        `${config.body.config?.shift_start} - ${config.body.config?.shift_end}`);

    const upcoming = await api("GET", "/admin/upcoming/AD001", { token });
    check("and so does the upcoming-days view",
        upcoming.body.shift === "09:00–18:00", JSON.stringify(upcoming.body.shift));

    // ── AND A REAL OVERRIDE IS STILL AN OVERRIDE ────────────────────────
    // Inheritance must not become "the global always wins", which would take
    // away every night-shift employee's own hours.
    const override = await api("POST", "/admin/config", {
        token,
        body: {
            employee_id: "AD001", shift_start: "22:00", shift_end: "06:00",
            screenshot_min_minutes: 3, screenshot_max_minutes: 10,
            screenshots_per_day: 12, upload_interval_minutes: 60,
            idle_threshold_seconds: 60, force_logout: false,
            verbose_logging: false, late_grace_minutes: 10,
        },
    });
    check("an explicit per-employee shift saves", override.status === 200,
        `${override.status} ${override.body.message || ""}`);

    const overridden = await api("GET", "/admin/config/AD001", { token });
    check("and it is the employee's own shift that comes back, not the global",
        String(overridden.body.config?.shift_start).startsWith("22:00"),
        `${overridden.body.config?.shift_start}`);

    const nightRow = await lateRow();
    check("and attendance measures against the override",
        nightRow?.shift_window === "22:00–06:00",
        JSON.stringify(nightRow?.shift_window));

    await pool.end();
    server.close();
    console.log(`\n${failures ? `${failures} FAILED` : "ALL PASS"}\n`);
    try { execFileSync("dropdb", ["--if-exists", DB]); } catch (_) {}
    process.exit(failures ? 1 : 0);
}

main().catch((error) => {
    console.error(error);
    try { execFileSync("dropdb", ["--if-exists", DB]); } catch (_) {}
    process.exit(1);
});
