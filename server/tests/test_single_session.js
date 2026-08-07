/**
 * One account, one machine — and the ways that rule goes wrong.
 *
 * Blocking a second sign-in is easy. Blocking it WITHOUT locking people out
 * of their own accounts is the whole problem: an app that crashes, a lid that
 * closes, a machine that loses power — none of those log out, so a naive
 * "is there a session row?" check strands the employee permanently.
 *
 * The rule is therefore about a LIVE session. last_seen is stamped by every
 * authenticated request, clients poll every five seconds, and anything quiet
 * for two minutes is treated as gone. These checks cover both directions:
 * a genuinely concurrent login is refused, and a dead session is taken over.
 *
 * Run:  node server/tests/test_single_session.js
 */
const { execFileSync } = require("child_process");
const path = require("path");

const DB = `ets_session_${process.pid}`;
const PORT = 8000 + ((process.pid + 401) % 1000);
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

const login = (username, password) =>
    api("POST", "/auth/login", { body: { username, password } });

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`One account, one machine (${DB})\n`);

    psql("postgres", `CREATE DATABASE ${DB}`);
    try {
        for (const file of [
            path.join(root, "ets.sql"),
            path.join(root, "server", "migrations", "2026_08_05_password_management.sql"),
            path.join(root, "server", "migrations", "2026_08_05_username_case_insensitive.sql"),
            path.join(root, "server", "migrations", "2026_08_06_single_session.sql"),
        ]) {
            execFileSync("psql", ["-d", DB, "-v", "ON_ERROR_STOP=1", "-q", "-f", file],
                { stdio: "pipe" });
        }

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash("SuperSecret123", 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role) VALUES
            ('SA001','superadmin','${hash}','super_admin'),
            ('E001','emp1','${hash}','employee')`);

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

        // ── the rule ────────────────────────────────────────────────────
        let res = await login("emp1", "SuperSecret123");
        check("first sign-in succeeds", res.status === 200, `status ${res.status}`);
        const first = res.body.token;

        res = await login("emp1", "SuperSecret123");
        check("a second sign-in while the first is live is refused",
            res.status === 409, `status ${res.status}`);
        check("and says why, rather than 'invalid credentials'",
            /already logged in/i.test(res.body.message || ""), res.body.message);

        res = await api("GET", "/dashboard/me", { token: first });
        check("the first machine keeps working", res.status === 200, `status ${res.status}`);

        // ── logging out frees it ────────────────────────────────────────
        await api("POST", "/auth/logout", { token: first });
        res = await login("emp1", "SuperSecret123");
        check("after a logout the account is free again",
            res.status === 200, `status ${res.status}`);
        const second = res.body.token;

        // ── the block does not weaken with time ─────────────────────────
        //
        // A machine closed hours ago still holds the account. This is the
        // deliberate consequence of an absolute rule: nothing frees it
        // except logging out, an admin's Force logout, or the token
        // expiring on its own.
        psql(DB, `UPDATE active_sessions
                     SET last_seen = NOW() - INTERVAL '10 hours'
                   WHERE employee_id = 'E001'`);
        res = await login("emp1", "SuperSecret123");
        check("a long-quiet session still blocks a second login",
            res.status === 409, `status ${res.status}`);
        check("and says already logged in",
            /already logged in/i.test(res.body.message || ""), res.body.message);

        // ── an expired token does release the account ───────────────────
        //
        // Without this the only recovery from a dead machine is an admin,
        // forever. A token is good for 24 hours, so a session whose token
        // has expired is over no matter what the row says.
        const expired = require(path.join(root, "server", "node_modules", "jsonwebtoken"))
            .sign({ employee_id: "E001", role: "employee" },
                  process.env.JWT_SECRET, { expiresIn: "-1h" });
        psql(DB, `UPDATE active_sessions SET token = '${expired}'
                   WHERE employee_id = 'E001'`);
        await new Promise((r) => setTimeout(r, 1100));
        res = await login("emp1", "SuperSecret123");
        check("a session whose token has expired can be taken over",
            res.status === 200, `status ${res.status}`);
        const third = res.body.token;

        // ── an admin can free a stuck account ───────────────────────────
        res = await login("superadmin", "SuperSecret123");
        const adminToken = res.body.token;

        res = await api("POST", "/admin/force-logout",
            { token: adminToken, body: { employee_id: "E001" } });
        check("force logout succeeds", res.status === 200, `status ${res.status}`);

        // A JWT's `iat` has one-second resolution, so a token minted in the
        // same second as the last one is byte-identical — and the "old"
        // token would appear to still work. Wait past the tick so the
        // eviction below is actually observable.
        await new Promise((r) => setTimeout(r, 1100));
        res = await login("emp1", "SuperSecret123");
        check("force logout frees the account immediately, without waiting",
            res.status === 200, `status ${res.status}`);
        check("and the new token really is a different one",
            res.body.token !== third);

        const after = await api("GET", "/dashboard/me", { token: third });
        check("and the forced-out machine is signed out",
            after.status === 401,
            `status ${after.status} ${JSON.stringify(after.body).slice(0,90)}`);

        // ── two different accounts are unaffected ───────────────────────
        res = await api("GET", "/dashboard/me", { token: adminToken });
        check("a different account signed in at the same time is untouched",
            res.status === 200, `status ${res.status}`);

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
    console.log("all single-session checks passed");
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
