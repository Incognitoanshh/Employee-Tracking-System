/**
 * Suspending an account.
 *
 * Force logout ends a session and nothing more — the person signs straight
 * back in, which makes it the wrong tool for somebody who should not be
 * working at all. Suspension is the state that persists.
 *
 * The checks that matter are the ones where a half-working suspension is
 * indistinguishable from a working one until it matters:
 *
 *   * it must bite on an ALREADY-OPEN session, not only at the next login.
 *     A suspension that takes effect in twenty-four hours is not one.
 *   * an admin must not be able to suspend another admin, and no one may
 *     suspend a super admin. Suspending upward is a takeover.
 *   * nobody may suspend themselves — for the last super admin that is
 *     unrecoverable without database access.
 *   * the message must say "suspended", not "invalid credentials", or the
 *     person keeps trying and locks the account instead.
 *
 * Run:  node server/tests/test_suspend.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_suspend_${process.pid}`;
const PORT = 8000 + ((process.pid + 313) % 1000);
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

const login = (u, p) => api("POST", "/auth/login", { body: { username: u, password: p } });
const suspend = (token, id, on) =>
    api("POST", `/admin/employees/${id}/suspend`, { token, body: { suspended: on } });

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`Suspending accounts (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash("SuperSecret123", 10);
        psql(DB, `INSERT INTO employees
                     (employee_id, username, password, role, full_name, designation) VALUES
            ('SA001','superadmin','${hash}','super_admin','Ansh Owner','Founder'),
            ('SA002','superadmin2','${hash}','super_admin','Second Owner','Founder'),
            ('A001','admin1','${hash}','admin','Priya Nair','Operations Manager'),
            ('A002','admin2','${hash}','admin','Second Admin','Operations'),
            ('E001','emp1','${hash}','employee','Rajesh Kumar','QA Engineer')`);

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

        const sa = (await login("superadmin", "SuperSecret123")).body.token;
        const admin = (await login("admin1", "SuperSecret123")).body.token;

        // ── an admin suspends an employee ───────────────────────────────
        let res = await login("emp1", "SuperSecret123");
        const empToken = res.body.token;
        check("the employee can sign in to begin with",
            res.status === 200, `status ${res.status}`);
        check("and their session works",
            (await api("GET", "/dashboard/me", { token: empToken })).status === 200);

        res = await suspend(admin, "E001", true);
        check("an admin suspends an employee", res.status === 200, `status ${res.status}`);

        // The important one: it must bite on the session already open.
        res = await api("GET", "/dashboard/me", { token: empToken });
        check("the OPEN session stops working immediately",
            res.status === 403, `status ${res.status}`);
        check("and says suspended, not 'session expired'",
            /suspended/i.test(res.body.message || ""), res.body.message);

        res = await login("emp1", "SuperSecret123");
        check("they cannot sign in again", res.status === 403, `status ${res.status}`);
        check("the message tells them why",
            /you are suspended/i.test(res.body.message || ""), res.body.message);
        check("and it is flagged so the client can act on it",
            res.body.suspended === true, JSON.stringify(res.body.suspended));

        // ── it persists ─────────────────────────────────────────────────
        psql(DB, `UPDATE active_sessions SET token = NULL WHERE employee_id = 'E001'`);
        res = await login("emp1", "SuperSecret123");
        check("clearing the session does not lift it",
            res.status === 403, `status ${res.status}`);
        check("suspended_by and suspended_at are recorded",
            psql(DB, `SELECT suspended_by FROM employees WHERE employee_id='E001'`) === "A001"
            && psql(DB, `SELECT suspended_at IS NOT NULL FROM employees WHERE employee_id='E001'`) === "t");

        // ── restoring ───────────────────────────────────────────────────
        res = await suspend(admin, "E001", false);
        check("an admin restores the account", res.status === 200, `status ${res.status}`);
        res = await login("emp1", "SuperSecret123");
        check("and they can sign in again", res.status === 200, `status ${res.status}`);
        // Kept for the role check below — one account can only be signed in
        // once, so logging in again there would be refused and the request
        // would arrive unauthenticated, proving nothing about the role rule.
        const empLive = res.body.token;
        check("the record of who suspended them is cleared",
            psql(DB, `SELECT COALESCE(suspended_by,'-') FROM employees WHERE employee_id='E001'`) === "-");

        // ── the hierarchy ───────────────────────────────────────────────
        res = await suspend(admin, "A002", true);
        check("an admin cannot suspend another admin", res.status === 403, `status ${res.status}`);
        check("and admin2 is untouched",
            psql(DB, `SELECT suspended FROM employees WHERE employee_id='A002'`) === "f");

        res = await suspend(admin, "SA001", true);
        check("an admin cannot suspend a super admin", res.status === 403, `status ${res.status}`);

        res = await suspend(sa, "A002", true);
        check("a super admin CAN suspend an admin", res.status === 200, `status ${res.status}`);
        res = await login("admin2", "SuperSecret123");
        check("and that admin cannot sign in", res.status === 403, `status ${res.status}`);
        await suspend(sa, "A002", false);

        res = await suspend(sa, "SA002", true);
        check("a super admin may suspend another super admin",
            res.status === 200, `status ${res.status}`);
        await suspend(sa, "SA002", false);

        // ── nobody suspends themselves ──────────────────────────────────
        res = await suspend(sa, "SA001", true);
        check("you cannot suspend your own account",
            res.status === 400, `status ${res.status}`);
        check("and the reason is stated",
            /your own account/i.test(res.body.message || ""), res.body.message);

        res = await api("POST", "/admin/employees/E001/suspend",
            { token: empLive, body: { suspended: true } });
        check("an employee cannot suspend anyone", res.status === 403, `status ${res.status}`);

        // ── the list carries the state, so the button knows its label ───
        res = await api("GET", "/admin/employees", { token: sa });
        const rows = res.body.employees || res.body.data || [];
        check("the employee list reports who is suspended",
            rows.length > 0 && rows.every((r) => "suspended" in r),
            JSON.stringify(Object.keys(rows[0] || {})));

        // ── finding somebody by the name you actually saw ───────────────
        //
        // Every other part of the product shows a person by their full name —
        // chat, reports, the audit log. Only this list showed the login
        // username, and it was also the only thing search matched. So an
        // admin who had just read a message from "Priya Nair" searched for
        // her here and was told there was no such person: the account is
        // A001 / admin1.
        res = await api("GET", "/admin/employees?search=Priya", { token: sa });
        let found = res.body.employees || res.body.data || [];
        check("searching the name somebody is shown by finds them",
            found.some((r) => r.employee_id === "A001"),
            JSON.stringify(found.map((r) => r.employee_id)));

        res = await api("GET", "/admin/employees?search=nair", { token: sa });
        found = res.body.employees || res.body.data || [];
        check("a surname on its own works, in any case",
            found.some((r) => r.employee_id === "A001"),
            JSON.stringify(found.map((r) => r.employee_id)));

        res = await api("GET", "/admin/employees?search=QA", { token: sa });
        found = res.body.employees || res.body.data || [];
        check("so does a job title — \"who are the QA people\" is a real question",
            found.some((r) => r.employee_id === "E001"),
            JSON.stringify(found.map((r) => r.employee_id)));

        res = await api("GET", "/admin/employees?search=admin1", { token: sa });
        found = res.body.employees || res.body.data || [];
        check("and the username still works — nothing was traded away",
            found.some((r) => r.employee_id === "A001"),
            JSON.stringify(found.map((r) => r.employee_id)));

        res = await api("GET", "/admin/employees?search=Priya", { token: sa });
        check("the count matches the filtered rows, not the whole table",
            Number(res.body.total ?? res.body.count ?? 1) === 1,
            JSON.stringify({ total: res.body.total, count: res.body.count }));

        res = await api("GET", "/admin/employees?search=zzz-nobody", { token: sa });
        found = res.body.employees || res.body.data || [];
        check("a search that matches nobody returns nobody",
            found.length === 0, JSON.stringify(found.map((r) => r.employee_id)));

        res = await api("GET", "/admin/employees", { token: sa });
        found = res.body.employees || res.body.data || [];
        check("the list carries the name, so the panel can show it",
            found.every((r) => "full_name" in r),
            JSON.stringify(Object.keys(found[0] || {})));

        // ── changing the name somebody is shown by ──────────────────────
        //
        // Until this endpoint existed there was no way to correct a name at
        // all, and every account made from the panel took the login username
        // because the create dialog never asked for one.
        res = await api("POST", "/admin/employees/E001/profile",
            { token: sa, body: { full_name: "Rajesh K. Kumar", designation: "Senior QA" } });
        check("an admin can correct a name", res.status === 200, `status ${res.status}`);
        check("and it comes back changed",
            res.body.employee.full_name === "Rajesh K. Kumar",
            JSON.stringify(res.body.employee));
        check("the designation with it",
            res.body.employee.designation === "Senior QA",
            JSON.stringify(res.body.employee));
        check("the login username is NOT touched — they still sign in the same way",
            psql(DB, `SELECT username FROM employees WHERE employee_id='E001'`) === "emp1");

        check("renaming somebody is on the record",
            Number(psql(DB, `SELECT COUNT(*) FROM activity_logs
                              WHERE activity LIKE 'NAME CHANGED%'`)) === 1);
        check("with both the old name and the new one",
            /Rajesh Kumar.*Rajesh K\. Kumar/.test(
                psql(DB, `SELECT activity FROM activity_logs
                           WHERE activity LIKE 'NAME CHANGED%' LIMIT 1`)),
            psql(DB, `SELECT activity FROM activity_logs
                       WHERE activity LIKE 'NAME CHANGED%' LIMIT 1`));

        res = await api("POST", "/admin/employees/E001/profile",
            { token: sa, body: { full_name: "   " } });
        check("an empty name is refused — it would go back to showing the login",
            res.status === 400, `status ${res.status}`);
        check("and the old one survives",
            psql(DB, `SELECT full_name FROM employees WHERE employee_id='E001'`)
                === "Rajesh K. Kumar");

        res = await api("POST", "/admin/employees/E001/profile",
            { token: sa, body: { full_name: "x".repeat(200) } });
        check("a name longer than the column is refused with a message, not a crash",
            res.status === 400, `status ${res.status}`);

        res = await api("POST", "/admin/employees/E001/profile",
            { token: empLive, body: { full_name: "Boss" } });
        check("an employee cannot rename anybody", res.status === 403, `status ${res.status}`);

        res = await api("POST", "/admin/employees/SA001/profile",
            { token: admin, body: { full_name: "Not The Owner" } });
        check("an ordinary admin cannot rename a super admin",
            res.status === 403, `status ${res.status}`);

        res = await api("POST", "/admin/employees/NOBODY/profile",
            { token: sa, body: { full_name: "Ghost" } });
        check("renaming somebody who does not exist is a 404",
            res.status === 404, `status ${res.status}`);

        // ── it is on the record ─────────────────────────────────────────
        check("suspending is written to the audit log",
            Number(psql(DB, `SELECT COUNT(*) FROM activity_logs
                              WHERE activity LIKE 'ACCOUNT SUSPENDED%'`)) >= 2);
        check("so is restoring",
            Number(psql(DB, `SELECT COUNT(*) FROM activity_logs
                              WHERE activity LIKE 'ACCOUNT UNSUSPENDED%'`)) >= 1);

        res = await suspend(sa, "NOSUCH", true);
        check("suspending a missing account gives 404", res.status === 404, `status ${res.status}`);

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
    console.log("all suspend checks passed");
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
