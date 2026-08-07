/**
 * Data retention — the setting that deletes things.
 *
 * Nothing was purged before this existed: retention_purge.sql had 90 and 180
 * days written into it and was never in cron, so activity_logs and
 * screenshots grew without limit until the disk filled.
 *
 * What is worth guarding here is not that the numbers save. It is that a
 * wrong number cannot quietly destroy the audit trail:
 *
 *   * a floor, so 1 day (or 0, or a blank) is refused rather than accepted
 *   * super admin only, because "delete everything older than a week" is not
 *     a control to hand to twenty admins
 *   * a preview of how much the current settings would remove, so nobody
 *     sets it blind
 *
 * Run:  node server/tests/test_retention.js
 */
const { execFileSync } = require("child_process");
const path = require("path");

const DB = `ets_retention_${process.pid}`;
const PORT = 8000 + ((process.pid + 733) % 1000);
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
    console.log(`Data retention (${DB})\n`);

    psql("postgres", `CREATE DATABASE ${DB}`);
    try {
        for (const file of [
            path.join(root, "ets.sql"),
            path.join(root, "server", "migrations", "2026_08_05_password_management.sql"),
            path.join(root, "server", "migrations", "2026_08_05_username_case_insensitive.sql"),
            path.join(root, "server", "migrations", "2026_08_06_single_session.sql"),
            path.join(root, "server", "migrations", "2026_08_06_app_settings.sql"),
        ]) {
            execFileSync("psql", ["-d", DB, "-v", "ON_ERROR_STOP=1", "-q", "-f", file],
                { stdio: "pipe" });
        }

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash("SuperSecret123", 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role) VALUES
            ('SA001','superadmin','${hash}','super_admin'),
            ('A001','admin1','${hash}','admin'),
            ('E001','emp1','${hash}','employee')`);

        // Old enough to be past the defaults, and recent enough not to be.
        psql(DB, `INSERT INTO activity_logs (employee_id, activity, created_at)
                  SELECT 'E001','old', NOW() - INTERVAL '200 days' FROM generate_series(1,5)`);
        psql(DB, `INSERT INTO activity_logs (employee_id, activity, created_at)
                  SELECT 'E001','recent', NOW() - INTERVAL '2 days' FROM generate_series(1,3)`);
        psql(DB, `INSERT INTO screenshots (employee_id, file_name, created_at)
                  SELECT 'E001','old.enc', NOW() - INTERVAL '400 days' FROM generate_series(1,4)`);

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
        const sa = res.body.token;

        // ── defaults and the preview ────────────────────────────────────
        res = await api("GET", "/admin/retention", { token: sa });
        check("retention reads back", res.status === 200, `status ${res.status}`);
        check("seeded with the periods the old script hardcoded",
            res.body.settings?.log_retention_days === 90
            && res.body.settings?.screenshot_retention_days === 180,
            JSON.stringify(res.body.settings));
        check("says how many logs are already past the period",
            res.body.would_delete?.activity_logs === 5,
            JSON.stringify(res.body.would_delete));
        check("and how many screenshots",
            res.body.would_delete?.screenshots === 4,
            JSON.stringify(res.body.would_delete));

        // ── the floors ──────────────────────────────────────────────────
        for (const [value, label] of [[1, "1 day"], [0, "zero"], [-5, "negative"]]) {
            res = await api("POST", "/admin/retention",
                { token: sa, body: { log_retention_days: value } });
            check(`a retention of ${label} is refused`,
                res.status === 400, `status ${res.status}`);
        }
        res = await api("POST", "/admin/retention",
            { token: sa, body: { log_retention_days: "" } });
        check("a blank value is refused", res.status === 400, `status ${res.status}`);

        res = await api("POST", "/admin/retention",
            { token: sa, body: { attendance_retention_days: 30 } });
        check("attendance has a higher floor than logs — payroll reads it",
            res.status === 400, `status ${res.status}`);

        // ── a valid change, and the preview moving with it ──────────────
        res = await api("POST", "/admin/retention",
            { token: sa, body: { log_retention_days: 30, screenshot_retention_days: 30 } });
        check("a sensible change saves", res.status === 200, `status ${res.status}`);

        res = await api("GET", "/admin/retention", { token: sa });
        check("the new period is what reads back",
            res.body.settings?.log_retention_days === 30,
            JSON.stringify(res.body.settings?.log_retention_days));
        check("and the preview grows to match it",
            res.body.would_delete?.activity_logs === 5
            && res.body.would_delete?.screenshots === 4,
            JSON.stringify(res.body.would_delete));

        // ── who may touch it ────────────────────────────────────────────
        res = await api("POST", "/auth/login",
            { body: { username: "admin1", password: "SuperSecret123" } });
        const admin = res.body.token;
        res = await api("GET", "/admin/retention", { token: admin });
        check("an ordinary admin cannot read it", res.status === 403, `status ${res.status}`);
        res = await api("POST", "/admin/retention",
            { token: admin, body: { log_retention_days: 7 } });
        check("nor change it", res.status === 403, `status ${res.status}`);

        res = await api("POST", "/auth/login",
            { body: { username: "emp1", password: "SuperSecret123" } });
        res = await api("GET", "/admin/retention", { token: res.body.token });
        check("an employee certainly cannot", res.status === 403, `status ${res.status}`);

        // ── the change is on the record ─────────────────────────────────
        const logged = psql(DB,
            `SELECT COUNT(*) FROM activity_logs WHERE activity LIKE 'RETENTION CHANGED%'`);
        check("changing retention is written to the audit log",
            Number(logged) >= 1, logged);

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
    console.log("all retention checks passed");
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
