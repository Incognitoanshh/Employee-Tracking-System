/**
 * Salary-triggered draft regeneration end-to-end integration test.
 *
 * Checks that setting a new salary automatically refreshes affected DRAFTs,
 * correctly recomputing daily rates and deductions, while strictly preserving
 * manually entered overtime hours and adjustments.
 *
 * Run:  node server/tests/test_salary_draft_integration.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { migrate } = require("./_migrate");

const DB = `ets_salary_draft_${process.pid}`;
const PORT = 8000 + ((process.pid + 631) % 1000);
const BASE = `http://127.0.0.1:${PORT}/api`;
const PASSWORD = "SuperSecret123";

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}

function psql(sql, db = DB) {
    return execFileSync("psql", ["-d", db, "-q", "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        { encoding: "utf8" }).trim();
}

async function api(method, route, { token, body, raw } = {}) {
    const response = await fetch(`${BASE}${route}`, {
        method,
        headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        ...(body && method !== "GET" ? { body: JSON.stringify(body) } : {}),
    });
    if (raw) return { status: response.status, text: await response.text() };
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return { status: response.status, body: payload };
}

const login = async (username, device) =>
    (await api("POST", "/auth/login",
        { body: { username, password: PASSWORD, device_id: device } })).body.token;

const near = (a, b) => Math.abs(Number(a) - Number(b)) < 0.02;

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    const uploads = fs.mkdtempSync(path.join(os.tmpdir(), "ets_salary_draft_"));
    console.log(`Salary/Draft Integration (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(`INSERT INTO employees (employee_id, username, password, role,
                                     full_name, email, created_at)
              VALUES ('A001','admin1','${hash}','admin','Admin','a@x.test','2025-01-01'),
                     ('E001','rajesh','${hash}','employee','Rajesh','r@x.test','2025-01-01')`);
        psql(`UPDATE employee_configs SET weekly_offs = '' WHERE employee_id IS NULL`);

        Object.assign(process.env, {
            DB_HOST: process.env.PGHOST || "127.0.0.1",
            DB_PORT: process.env.PGPORT || "5432",
            DB_NAME: DB,
            DB_USER: process.env.PGUSER || process.env.USER,
            DB_PASSWORD: process.env.PGPASSWORD || "unused-locally",
            JWT_SECRET: "test-secret",
            PORT: String(PORT),
            ENCRYPTION_KEY: "0".repeat(64),
            UPLOAD_DIR: uploads,
        });

        const { server, pool } = require(path.join(root, "server", "server.js"));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        const admin = await login("admin1", "admin-machine");
        
        console.log("Setting up initial state");
        // Set initial salary (30k/month = 1000/day, OT 100/hr)
        await api("POST", "/admin/payroll/salaries", { token: admin, body: {
            employee_id: "E001", gross_monthly: 30000, overtime_hourly: 100,
            effective_from: "2026-01-01" } });

        const MONTH = "2026-06"; // 30 days
        // 20 days present, 1 day unpaid leave
        for (let d = 1; d <= 20; d += 1) {
            const day = String(d).padStart(2, "0");
            psql(`INSERT INTO attendance (employee_id, login_time, logout_time)
                  VALUES ('E001','2026-06-${day} 04:00:00','2026-06-${day} 12:00:00')`);
        }
        psql(`INSERT INTO leave_requests (employee_id, leave_type, reason, start_date, end_date, total_days, status, approved_by)
              VALUES ('E001','UNPAID','personal','2026-06-21','2026-06-21',1,'APPROVED','A001')`);

        // Generate draft
        await api("POST", "/admin/payroll/generate", { token: admin, body: { month: MONTH } });

        // Add 10 hours overtime
        await api("POST", `/admin/payroll/${MONTH}/overtime`, { token: admin, body: { employee_id: "E001", hours: 10 } });

        // Add a 500 manual deduction
        await api("POST", `/admin/payroll/${MONTH}/adjustments`, { token: admin, body: { employee_id: "E001", kind: "FINE", amount: 500, reason: "Test fine" } });

        // Verify initial state
        let res = await api("GET", `/admin/payroll/${MONTH}`, { token: admin });
        let line = res.body.lines.find(l => l.employee_id === "E001");
        check("Initial gross is 30k", near(line.gross_monthly, 30000));
        check("Initial unpaid deduction is 1000", near(line.unpaid_deduction, 1000)); // 1 day
        check("Initial overtime amount is 1000", near(line.overtime_amount, 1000)); // 10 hrs * 100
        check("Initial adjustments is 500 deduction", near(line.adjustments_total, -500));

        console.log("\nChanging salary and triggering atomic rebuild");
        // New salary: 60k/month = 2000/day, OT 200/hr
        let setSalRes = await api("POST", "/admin/payroll/salaries", { token: admin, body: {
            employee_id: "E001", gross_monthly: 60000, overtime_hourly: 200,
            effective_from: "2026-06-01" } });

        check("Salary update succeeded", setSalRes.status === 200);
        check("Response contains refreshed_drafts", setSalRes.body.refreshed_drafts && setSalRes.body.refreshed_drafts.includes(MONTH));

        // Fetch rebuilt draft
        res = await api("GET", `/admin/payroll/${MONTH}`, { token: admin });
        line = res.body.lines.find(l => l.employee_id === "E001");
        
        check("Gross updated to 60k", near(line.gross_monthly, 60000));
        check("Per day rate updated to 2000", near(line.per_day, 2000));
        check("Unpaid deduction updated to 2000", near(line.unpaid_deduction, 2000)); // 1 day
        check("Overtime hours preserved (10)", near(line.overtime_hours, 10));
        check("Overtime amount recalculated (10 * 200 = 2000)", near(line.overtime_amount, 2000));
        check("Manual adjustment strictly preserved", near(line.adjustments_total, -500));

        console.log("\nFinalized months are not modified");
        await api("POST", `/admin/payroll/${MONTH}/finalize`, { token: admin });
        await api("POST", "/admin/payroll/salaries", { token: admin, body: {
            employee_id: "E001", gross_monthly: 90000, overtime_hourly: 300,
            effective_from: "2026-06-01" } });
        
        res = await api("GET", `/admin/payroll/${MONTH}`, { token: admin });
        line = res.body.lines.find(l => l.employee_id === "E001");
        check("Gross remains 60k for finalized month", near(line.gross_monthly, 60000));

        // ── THE ERROR PATH ──────────────────────────────────────────────
        //
        // Everything above is the happy path, and the fault that mattered was
        // not on it. The save runs in a transaction, and the catch that was
        // meant to roll it back referred to `client` — declared as a const
        // INSIDE the try, so out of scope in the catch. Any error part-way
        // through a save threw ReferenceError from the error handler, and the
        // connection was never rolled back and never released.
        //
        // Measured before the fix: the pool went from 3 idle to 2 idle and
        // stayed there, and pool.end() then waited on it for ever. The pool is
        // ten connections, so ten failed saves is a server that hangs on every
        // request that touches the database.
        //
        // The failure is forced with a component name longer than its
        // VARCHAR(60) column — a real error, raised inside the transaction.
        console.log("\nA save that fails part-way through");
        const idleBefore = pool.idleCount;
        const auditsBefore = Number(psql(
            `SELECT count(*) FROM activity_logs WHERE activity LIKE 'SALARY SET%'`));
        const salariesBefore = Number(psql(`SELECT count(*) FROM employee_salaries`));

        const broken = await api("POST", "/admin/payroll/salaries", { token: admin, body: {
            employee_id: "E001", ctc_annual: 600000, overtime_hourly: 100,
            effective_from: "2026-07-01",
            components: [{ name: "X".repeat(100), rule: "PERCENT_CTC", value: 50 },
                         { name: "Rest", rule: "BALANCE", value: 0 }] } });
        check("the failing save is answered, not left hanging",
            broken.status >= 400, `HTTP ${broken.status}`);

        // Released connections go back to idle on the next tick.
        await new Promise((r) => setTimeout(r, 300));
        check("the connection it used is given back to the pool",
            pool.idleCount === idleBefore,
            `idle ${idleBefore} before, ${pool.idleCount} after — one was never released`);
        check("and no transaction is left open on the database",
            Number(psql(`SELECT count(*) FROM pg_stat_activity
                          WHERE datname = '${DB}'
                            AND state LIKE 'idle in transaction%'`)) === 0);
        check("nothing of the failed save is kept",
            Number(psql(`SELECT count(*) FROM employee_salaries`)) === salariesBefore);
        // THE AUDIT IS WRITTEN AFTER THE COMMIT. It used to be written from
        // inside the transaction through the pool, so it survived the
        // rollback and claimed a salary had been set when nothing was saved.
        check("and the audit log does not claim a salary was set",
            Number(psql(`SELECT count(*) FROM activity_logs
                          WHERE activity LIKE 'SALARY SET%'`)) === auditsBefore);

        const after = await api("POST", "/admin/payroll/salaries", { token: admin, body: {
            employee_id: "E001", gross_monthly: 61000, overtime_hourly: 200,
            effective_from: "2026-07-01" } });
        check("and the next save on the same pool works",
            after.status === 200 && after.body.success, `HTTP ${after.status}`);

        server.close();
        await pool.end();
    } finally {
        try { psql(`DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`, "postgres"); } catch (_) {}
        try { fs.rmSync(uploads, { recursive: true, force: true }); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.stdout.write("", () => process.exit(1));
    } else {
        console.log("all integration checks passed");
        process.stdout.write("", () => process.exit(0));
    }
}

main().catch((error) => {
    console.error(error);
    try { psql(`DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`, "postgres"); } catch (_) {}
    process.exit(1);
});
