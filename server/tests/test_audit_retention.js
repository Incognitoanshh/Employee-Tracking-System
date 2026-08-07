/**
 * Two retention periods, and the report that depends on the longer one.
 *
 * activity_logs holds two different things. Almost all of it is volume —
 * idle flips, sign-ins, scheduler chatter — useful for a week and worthless
 * after a month. A small part records what an administrator DID.
 *
 * A single period cannot serve both, and getting that wrong is quiet: set 31
 * days because the table is large, and two months later "who reset that
 * password" has no answer anywhere and nobody noticed it went.
 *
 * So the checks here are mostly about the split holding — noise going on the
 * short period while the evidence stays — and about the audit report
 * reporting only the evidence.
 *
 * Run:  node server/tests/test_audit_retention.js
 */
const { execFileSync } = require("child_process");
const path = require("path");

const DB = `ets_audit_${process.pid}`;
const PORT = 8000 + ((process.pid + 191) % 1000);
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
    console.log(`Audit retention and the audit report (${DB})\n`);

    psql("postgres", `CREATE DATABASE ${DB}`);
    try {
        for (const file of [
            path.join(root, "ets.sql"),
            path.join(root, "server", "migrations", "2026_08_05_password_management.sql"),
            path.join(root, "server", "migrations", "2026_08_05_username_case_insensitive.sql"),
            path.join(root, "server", "migrations", "2026_08_06_single_session.sql"),
            path.join(root, "server", "migrations", "2026_08_06_app_settings.sql"),
            path.join(root, "server", "migrations", "2026_08_06_audit_retention.sql"),
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

        // Noise and evidence, at the same ages.
        psql(DB, `INSERT INTO activity_logs (employee_id, activity, created_at)
                  SELECT 'E001','USER IDLE (75.0s)', NOW() - INTERVAL '60 days'
                    FROM generate_series(1,40)`);
        psql(DB, `INSERT INTO activity_logs (employee_id, activity, created_at)
                  SELECT 'E001','LOGIN SUCCESS : emp1', NOW() - INTERVAL '2 days'
                    FROM generate_series(1,5)`);
        psql(DB, `INSERT INTO activity_logs (employee_id, activity, created_at) VALUES
            ('A001','PASSWORD RESET : by A001',                 NOW() - INTERVAL '60 days'),
            ('A001','SCREENSHOTS DELETED : 3 capture(s) of E001', NOW() - INTERVAL '45 days'),
            ('SA001','RETENTION CHANGED : log_retention_days=31', NOW() - INTERVAL '3 days'),
            ('A001','PASSWORD RESET : by A001',                 NOW() - INTERVAL '1 days')`);

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

        // ── the split ───────────────────────────────────────────────────
        const { auditRowsSql } = require(path.join(root, "server", "utils", "audit_events"));
        const noiseOld = psql(DB,
            `SELECT COUNT(*) FROM activity_logs
              WHERE created_at < NOW() - INTERVAL '31 days' AND NOT (${auditRowsSql()})`);
        const auditOld = psql(DB,
            `SELECT COUNT(*) FROM activity_logs
              WHERE created_at < NOW() - INTERVAL '31 days' AND (${auditRowsSql()})`);
        check("noise older than 31 days is found", Number(noiseOld) === 40, noiseOld);
        check("and the administrative rows are told apart from it",
            Number(auditOld) === 2, auditOld);

        // Simulate the purge's two statements at a 31-day noise period.
        psql(DB, `DELETE FROM activity_logs
                   WHERE created_at < NOW() - INTERVAL '31 days'
                     AND NOT (${auditRowsSql()})`);
        check("the noise goes",
            psql(DB, `SELECT COUNT(*) FROM activity_logs WHERE activity LIKE 'USER IDLE%'`) === "0");
        check("the password reset from 60 days ago SURVIVES",
            Number(psql(DB,
                `SELECT COUNT(*) FROM activity_logs WHERE activity LIKE 'PASSWORD RESET%'`)) === 2);
        check("so does the screenshot deletion from 45 days ago",
            Number(psql(DB,
                `SELECT COUNT(*) FROM activity_logs WHERE activity LIKE 'SCREENSHOTS DELETED%'`)) === 1);
        check("and recent sign-ins are untouched",
            Number(psql(DB,
                `SELECT COUNT(*) FROM activity_logs WHERE activity LIKE 'LOGIN SUCCESS%'`)) === 5);

        // ── the report ──────────────────────────────────────────────────
        const today = psql(DB, `SELECT (NOW() AT TIME ZONE 'Asia/Kolkata')::date::text`);
        const weekAgo = psql(DB,
            `SELECT ((NOW() AT TIME ZONE 'Asia/Kolkata')::date - 7)::text`);

        res = await api("GET", `/admin/reports/audit?from=${weekAgo}&to=${today}`, { token: sa });
        check("the audit report responds", res.status === 200, `status ${res.status}`);
        // Two seeded inside the week. Deliberately read BEFORE this test
        // changes a retention setting — that write is itself an audited
        // action and would land in the range, which is correct behaviour and
        // would make a hardcoded number here wrong for the right reason.
        check("a week covers the two recent administrative actions",
            res.body.total === 2, JSON.stringify(res.body.total));
        check("and none of the sign-ins or idle flips",
            !JSON.stringify(res.body.entries).includes("LOGIN SUCCESS"),
            JSON.stringify(res.body.entries).slice(0, 100));
        check("actions are grouped by what they were",
            (res.body.by_action || []).some((a) => a.action === "PASSWORD RESET"),
            JSON.stringify(res.body.by_action));
        check("and by who did them",
            (res.body.by_person || []).some((p) => p.username === "admin1"),
            JSON.stringify(res.body.by_person));
        check("each entry says when, in IST",
            /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(res.body.entries?.[0]?.at || ""),
            res.body.entries?.[0]?.at);

        const wide = psql(DB, `SELECT ((NOW() AT TIME ZONE 'Asia/Kolkata')::date - 90)::text`);
        res = await api("GET", `/admin/reports/audit?from=${wide}&to=${today}`, { token: sa });
        check("a 90 day range still finds the 60-day-old reset — it was kept",
            res.body.total === 4, JSON.stringify(res.body.total));

        // ── the setting is real, and bounded ────────────────────────────
        res = await api("GET", "/admin/retention", { token: sa });
        check("audit retention reads back with the others",
            res.body.settings?.audit_log_retention_days === 730,
            JSON.stringify(res.body.settings));

        res = await api("POST", "/admin/retention",
            { token: sa, body: { log_retention_days: 31 } });
        check("31 days for the noise is accepted", res.status === 200, `status ${res.status}`);

        res = await api("POST", "/admin/retention",
            { token: sa, body: { audit_log_retention_days: 30 } });
        check("audit retention cannot be dropped below its floor",
            res.status === 400, `status ${res.status}`);

        // ── who may read it ─────────────────────────────────────────────
        res = await api("POST", "/auth/login",
            { body: { username: "admin1", password: "SuperSecret123" } });
        res = await api("GET", `/admin/reports/audit?from=${weekAgo}&to=${today}`,
            { token: res.body.token });
        check("an ordinary admin cannot read a report about admins",
            res.status === 403, `status ${res.status}`);

        res = await api("GET", `/admin/reports/audit?from=nope&to=${today}`, { token: sa });
        check("a malformed date is refused", res.status === 400, `status ${res.status}`);

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
    console.log("all audit retention checks passed");
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
