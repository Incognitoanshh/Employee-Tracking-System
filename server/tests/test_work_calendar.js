/**
 * Weekly offs and holidays, end to end.
 *
 * The thing worth guarding is not the CRUD — it is what /config/sync sends,
 * because that payload is the only way the client ever learns a day is not a
 * working day. If it goes missing or arrives in a different shape, nothing
 * fails anywhere: the client simply treats every day as a working day and
 * keeps capturing through the weekend, exactly as it did before this
 * feature existed. A green screen and wrong behaviour.
 *
 * Also pinned here: weekly offs come back in a stable, sorted, de-duplicated
 * form. The client diffs this string on a five second poll and rebuilds its
 * schedule whenever it moves, so an unstable ordering would rebuild the day's
 * schedule twelve times a minute.
 *
 * Run:  node server/tests/test_work_calendar.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_caltest_${process.pid}`;
const PORT = 8000 + ((process.pid + 137) % 1000);
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
    console.log(`Weekly offs and holidays, against a scratch database (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const seeded = await bcrypt.hash("SuperSecret123", 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role)
                  VALUES ('SA001', 'superadmin', '${seeded}', 'super_admin')`);

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
        check("super admin logs in", res.status === 200, `status ${res.status}`);

        await api("POST", "/admin/employees", { token, body: {
            employee_id: "E001", username: "emp1",
            password: "FirstPass99", role: "employee" } });
        res = await api("POST", "/auth/login",
            { body: { username: "emp1", password: "FirstPass99" } });
        const empToken = res.body.token;

        const sync = () => api("POST", "/config/sync",
            { token: empToken, body: { employee_id: "E001", device_id: "d" } });

        // ── nothing configured: every day is a working day ───────────────
        res = await sync();
        check("sync sends an empty calendar by default",
            res.body.config?.weekly_offs === "" && res.body.config?.holidays === "",
            JSON.stringify({ w: res.body.config?.weekly_offs, h: res.body.config?.holidays }));

        // ── weekly offs, global ─────────────────────────────────────────
        res = await api("POST", "/admin/config", { token, body: {
            employee_id: "global", screenshot_min_minutes: 3,
            screenshot_max_minutes: 10, screenshots_per_day: 10,
            upload_interval_minutes: 60, idle_threshold_seconds: 60,
            weekly_offs: [7] } });
        check("global weekly off saves", res.status === 200, JSON.stringify(res.body));

        res = await sync();
        check("an employee with no override inherits the global weekly off",
            res.body.config?.weekly_offs === "7", String(res.body.config?.weekly_offs));

        // ── the stable-ordering guarantee ────────────────────────────────
        await api("POST", "/admin/config", { token, body: {
            employee_id: "global", screenshot_min_minutes: 3,
            screenshot_max_minutes: 10, screenshots_per_day: 10,
            upload_interval_minutes: 60, idle_threshold_seconds: 60,
            weekly_offs: [7, 6, 7, 6] } });
        res = await sync();
        check("weekly offs are sorted and de-duplicated",
            res.body.config?.weekly_offs === "6,7", String(res.body.config?.weekly_offs));

        res = await api("POST", "/admin/config", { token, body: {
            employee_id: "global", screenshot_min_minutes: 3,
            screenshot_max_minutes: 10, screenshots_per_day: 10,
            upload_interval_minutes: 60, idle_threshold_seconds: 60,
            weekly_offs: [1, 2, 3, 4, 5, 6, 7] } });
        check("every day off is refused", res.status === 400, `status ${res.status}`);

        // ── a per-employee override wins, including an empty one ─────────
        res = await api("POST", "/admin/config", { token, body: {
            employee_id: "E001", screenshot_min_minutes: 3,
            screenshot_max_minutes: 10, screenshots_per_day: 10,
            upload_interval_minutes: 60, idle_threshold_seconds: 60,
            weekly_offs: [1] } });
        check("a per-employee weekly off saves", res.status === 200, JSON.stringify(res.body));
        res = await sync();
        check("the employee's own weekly off wins over the global one",
            res.body.config?.weekly_offs === "1", String(res.body.config?.weekly_offs));

        await api("POST", "/admin/config", { token, body: {
            employee_id: "E001", screenshot_min_minutes: 3,
            screenshot_max_minutes: 10, screenshots_per_day: 10,
            upload_interval_minutes: 60, idle_threshold_seconds: 60,
            weekly_offs: [] } });
        res = await sync();
        check("an employee set to NO weekly off does not inherit the global one",
            res.body.config?.weekly_offs === "", String(res.body.config?.weekly_offs));

        // ── holidays ────────────────────────────────────────────────────
        const today = psql(DB, "SELECT to_char(NOW() AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD')");
        const soon = psql(DB, "SELECT to_char((NOW() AT TIME ZONE 'Asia/Kolkata') + INTERVAL '10 days', 'YYYY-MM-DD')");
        const far  = psql(DB, "SELECT to_char((NOW() AT TIME ZONE 'Asia/Kolkata') + INTERVAL '200 days', 'YYYY-MM-DD')");

        res = await api("POST", "/admin/holidays", { token, body: {
            holiday_date: "not-a-date", name: "Nope" } });
        check("a malformed date is refused", res.status === 400, `status ${res.status}`);

        res = await api("POST", "/admin/holidays", { token, body: {
            holiday_date: soon, name: "" } });
        check("a holiday with no name is refused", res.status === 400, `status ${res.status}`);

        res = await api("POST", "/admin/holidays", { token, body: {
            holiday_date: soon, name: "Independence Day" } });
        check("a holiday saves", res.status === 200, JSON.stringify(res.body));

        await api("POST", "/admin/holidays", { token, body: {
            holiday_date: far, name: "Next year sometime" } });

        res = await sync();
        const sent = String(res.body.config?.holidays || "").split(",").filter(Boolean);
        check("a nearby holiday reaches the client", sent.includes(soon), sent.join("|"));
        check("a holiday months away does not", !sent.includes(far), sent.join("|"));

        res = await api("POST", "/admin/holidays", { token, body: {
            holiday_date: soon, name: "Renamed" } });
        res = await api("GET", "/admin/holidays", { token });
        const renamed = res.body.holidays?.find((h) => h.holiday_date === soon);
        check("saving the same date renames rather than duplicating",
            renamed?.name === "Renamed"
            && res.body.holidays.filter((h) => h.holiday_date === soon).length === 1,
            JSON.stringify(renamed));

        res = await api("DELETE", `/admin/holidays/${soon}`, { token });
        check("a holiday can be removed", res.status === 200, `status ${res.status}`);
        res = await api("DELETE", `/admin/holidays/${soon}`, { token });
        check("removing it twice gives 404", res.status === 404, `status ${res.status}`);

        res = await sync();
        check("the removed holiday stops being sent",
            !String(res.body.config?.holidays || "").includes(soon),
            String(res.body.config?.holidays));

        // ── an employee cannot manage the calendar ───────────────────────
        res = await api("GET", "/admin/holidays", { token: empToken });
        check("an employee cannot read the holiday list", res.status === 403, `status ${res.status}`);
        res = await api("POST", "/admin/holidays",
            { token: empToken, body: { holiday_date: today, name: "Day off for me" } });
        check("an employee cannot add a holiday", res.status === 403, `status ${res.status}`);

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
    console.log("all work calendar checks passed");
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
