/**
 * Password change and reset, end to end.
 *
 * Runs the real Express app against a scratch PostgreSQL database and talks
 * to it over HTTP, so route order, middleware, rate limiters and the role
 * hierarchy are all exercised as production has them — not stubbed.
 *
 * The parts worth guarding are the ones that fail quietly:
 *
 *   * a password change must sign out other devices. `active_sessions` holds
 *     one token per employee, so this only works if the new token is written
 *     there. Get it wrong and a stolen token keeps working until it expires
 *     on its own a day later, which no manual test would ever notice.
 *   * the device doing the changing must NOT be signed out, or every
 *     employee who changes their password gets kicked to the login screen
 *     and assumes it failed.
 *   * an admin must not be able to reset another admin's password. That is
 *     a privilege escalation: reset it, log in as them, and you are them.
 *
 * Run:  node server/tests/test_password.js
 *       (needs a reachable PostgreSQL that can CREATE DATABASE)
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_pwtest_${process.pid}`;
const PORT = 8000 + (process.pid % 1000);
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

/**
 * Sign in, freeing the account first.
 *
 * One account can only be signed in on one machine at a time now, so a test
 * that signs the same employee in repeatedly has to release the session
 * between attempts — otherwise every login after the first is correctly
 * refused with 409 and the check under test never runs.
 */
async function freshLogin(username, password, heldToken) {
    if (heldToken) await api("POST", "/auth/logout", { token: heldToken });
    return api("POST", "/auth/login", { body: { username, password } });
}

async function main() {
    const root = path.resolve(__dirname, "..", "..");

    console.log(`Password endpoints, against a scratch database (${DB})\n`);

    try {
        migrate(DB);

        // Seed a super admin directly — the API needs one to exist before it
        // can create anybody else.
        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const seeded = await bcrypt.hash("SuperSecret123", 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role)
                  VALUES ('SA001', 'superadmin', '${seeded}', 'super_admin')`);

        // server.js refuses to start with any of these blank, so they are
        // filled in even where a local trust-auth socket ignores them.
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

        // ── the super admin signs in ────────────────────────────────────
        let res = await api("POST", "/auth/login",
            { body: { username: "superadmin", password: "SuperSecret123" } });
        check("super admin logs in", res.status === 200, `status ${res.status}`);
        check("login reports must_change_password=false",
            res.body.must_change_password === false, String(res.body.must_change_password));
        const saToken = res.body.token;

        // ── account creation now enforces the password policy ───────────
        res = await api("POST", "/admin/employees", { token: saToken, body: {
            employee_id: "EW1", username: "weakling", password: "123", role: "employee" } });
        check("creating an account with a short password is rejected",
            res.status === 400, `status ${res.status}`);

        res = await api("POST", "/admin/employees", { token: saToken, body: {
            employee_id: "EW2", username: "obvious", password: "password123", role: "employee" } });
        check("creating an account with an obvious password is rejected",
            res.status === 400, `status ${res.status}`);

        res = await api("POST", "/admin/employees", { token: saToken, body: {
            employee_id: "E001", username: "emp1", password: "FirstPass99", role: "employee" } });
        check("creating an account with a good password succeeds",
            res.status === 200, `status ${res.status} ${res.body.message || ""}`);

        // ── the employee signs in on two devices ────────────────────────
        // NOTE: one account can no longer be signed in on two machines at
        // once — see test_single_session.js. So "the other device" here is a
        // session that was live earlier and has since been replaced, which
        // is the same thing from the token's point of view: an old token
        // that must stop working.
        res = await api("POST", "/auth/login",
            { body: { username: "emp1", password: "FirstPass99" } });
        const deviceA = res.body.token;
        check("employee signs in", res.status === 200, `status ${res.status}`);

        await api("POST", "/auth/logout", { token: deviceA });
        // A JWT's `iat` has one-second resolution: without the wait the
        // replacement token is byte-identical and nothing below is
        // observable.
        await new Promise((r) => setTimeout(r, 1100));
        res = await api("POST", "/auth/login",
            { body: { username: "emp1", password: "FirstPass99" } });
        const deviceB = res.body.token;
        check("signs in again on another machine after logging out",
            res.status === 200 && deviceB && deviceB !== deviceA,
            `status ${res.status}`);

        // ── changing your own password ──────────────────────────────────
        res = await api("POST", "/auth/password", { token: deviceB, body: {
            current_password: "WrongPass99", new_password: "SecondPass99" } });
        check("wrong current password is rejected", res.status === 401, `status ${res.status}`);

        res = await api("POST", "/auth/password", { token: deviceB, body: {
            current_password: "FirstPass99", new_password: "short" } });
        check("a new password below the minimum is rejected",
            res.status === 400, `status ${res.status}`);

        res = await api("POST", "/auth/password", { token: deviceB, body: {
            current_password: "FirstPass99", new_password: "FirstPass99" } });
        check("reusing the current password is rejected",
            res.status === 400, `status ${res.status}`);

        // Same one-second `iat` resolution as above: without the wait the
        // "new" token comes back byte-identical to the one that was just
        // used, and token rotation cannot be observed at all.
        await new Promise((r) => setTimeout(r, 1100));
        res = await api("POST", "/auth/password", { token: deviceB, body: {
            current_password: "FirstPass99", new_password: "SecondPass99" } });
        check("a valid change succeeds", res.status === 200, `status ${res.status}`);
        const deviceBNew = res.body.token;
        check("the change returns a fresh token", Boolean(deviceBNew) && deviceBNew !== deviceB);

        // ── who is still signed in ──────────────────────────────────────
        res = await api("GET", "/dashboard/me", { token: deviceBNew });
        check("the device that changed the password stays signed in",
            res.status === 200, `status ${res.status}`);

        res = await api("GET", "/dashboard/me", { token: deviceB });
        check("the token used to make the change is now dead",
            res.status === 401, `status ${res.status}`);

        res = await api("GET", "/dashboard/me", { token: deviceA });
        check("the earlier session's token is dead too",
            res.status === 401, `status ${res.status}`);

        res = await api("POST", "/auth/login",
            { body: { username: "emp1", password: "FirstPass99" } });
        check("the old password no longer works", res.status === 401, `status ${res.status}`);

        res = await freshLogin("emp1", "SecondPass99", deviceBNew);
        check("the new password works", res.status === 200, `status ${res.status}`);
        let empToken = res.body.token;

        // ── an admin resets a forgotten password ────────────────────────
        res = await api("POST", "/admin/employees", { token: saToken, body: {
            employee_id: "A001", username: "admin1", password: "AdminPass99", role: "admin" } });
        check("super admin creates an admin", res.status === 200, `status ${res.status}`);
        res = await api("POST", "/auth/login",
            { body: { username: "admin1", password: "AdminPass99" } });
        const adminToken = res.body.token;

        res = await api("POST", "/admin/employees/E001/password", { token: adminToken });
        check("admin resets an employee's password", res.status === 200, `status ${res.status}`);
        const temporary = res.body.temporary_password;
        check("a temporary password is returned once",
            typeof temporary === "string" && temporary.length >= 8, String(temporary));

        res = await api("GET", "/dashboard/me", { token: deviceBNew });
        check("the reset signs the employee out everywhere",
            res.status === 401, `status ${res.status}`);

        res = await freshLogin("emp1", temporary, empToken);
        check("the temporary password signs in", res.status === 200, `status ${res.status}`);
        check("login demands a password change",
            res.body.must_change_password === true, String(res.body.must_change_password));
        const tempToken = res.body.token;

        res = await api("POST", "/auth/password", { token: tempToken, body: {
            current_password: temporary, new_password: "ThirdPass99" } });
        check("the employee replaces the temporary password",
            res.status === 200, `status ${res.status}`);

        res = await freshLogin("emp1", "ThirdPass99", tempToken);
        check("the flag clears once they choose their own",
            res.body.must_change_password === false, String(res.body.must_change_password));
        empToken = res.body.token;

        // ── the hierarchy holds ─────────────────────────────────────────
        res = await api("POST", "/admin/employees", { token: saToken, body: {
            employee_id: "A002", username: "admin2", password: "AdminTwo99", role: "admin" } });
        check("super admin creates a second admin", res.status === 200, `status ${res.status}`);

        res = await api("POST", "/admin/employees/A002/password", { token: adminToken });
        check("an admin cannot reset another admin's password",
            res.status === 403, `status ${res.status}`);

        res = await api("POST", "/admin/employees/SA001/password", { token: adminToken });
        check("an admin cannot reset the super admin's password",
            res.status === 403, `status ${res.status}`);

        res = await api("POST", "/admin/employees/A002/password", { token: saToken });
        check("a super admin can reset an admin's password",
            res.status === 200, `status ${res.status}`);

        const empRes = await freshLogin("emp1", "ThirdPass99", empToken);
        res = await api("POST", "/admin/employees/A002/password",
            { token: empRes.body.token });
        check("an employee cannot reset anybody's password",
            res.status === 403, `status ${res.status}`);

        res = await api("POST", "/admin/employees/NOSUCH/password", { token: saToken });
        check("resetting a missing account gives 404", res.status === 404, `status ${res.status}`);

        // ── usernames are matched without case ──────────────────────────
        //
        // The super admin registered as "Amazeinternet" could not sign in by
        // typing "amazeinternet" — right password, "Invalid credentials", no
        // way to tell the difference from the outside.
        for (const attempt of ["EMP1", "Emp1", "eMp1", "  emp1  "]) {
            res = await freshLogin(attempt, "ThirdPass99", empToken);
            check(`username ${JSON.stringify(attempt)} signs in`,
                res.status === 200, `status ${res.status}`);
            empToken = res.body.token;
        }

        // The dangerous half: if a case-variant account could be created,
        // a login would match two rows and the planner would pick the winner.
        res = await api("POST", "/admin/employees", { token: saToken, body: {
            employee_id: "E999", username: "EMP1",
            password: "LookAlike99", role: "employee" } });
        check("a username differing only by case cannot be created",
            res.status === 409, `status ${res.status}`);

        res = await api("POST", "/auth/login",
            { body: { username: "emp1", password: "LookAlike99" } });
        check("and the lookalike's password does not work",
            res.status === 401, `status ${res.status}`);

        // ── the hash never leaves the database ──────────────────────────
        res = await api("GET", "/admin/employees", { token: saToken });
        const serialised = JSON.stringify(res.body);
        check("the employee list never exposes a password hash",
            !serialised.includes("$2a$") && !serialised.includes("$2b$"));

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
    console.log("all password checks passed");
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
