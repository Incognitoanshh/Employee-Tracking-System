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
const { migrate } = require("./_migrate");

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

const login = (username, password, device_id) =>
    api("POST", "/auth/login",
        { body: device_id ? { username, password, device_id } : { username, password } });

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`One account, one machine (${DB})\n`);

    try {
        migrate(DB);

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


        // ── the same machine coming back ────────────────────────────────
        console.log("\nThe same machine, and a second one");
        // The rule is "one machine at a time", not "one login until the token
        // expires". Closing the app without signing out — a crash, a closed
        // lid, quitting — leaves the session live, and the check used to see
        // that as a second machine and lock somebody out of their own laptop.
        await psql(DB, `UPDATE active_sessions SET token = NULL WHERE employee_id = 'E001'`);

        const LAPTOP = "laptop-aaaa-1111";
        const DESKTOP = "desktop-bbbb-2222";

        res = await login("emp1", "SuperSecret123", LAPTOP);
        check("signing in from a machine works", res.status === 200, `status ${res.status}`);
        const onLaptop = res.body.token;
        check("and the machine is recorded",
            psql(DB, `SELECT device_id FROM active_sessions WHERE employee_id='E001'`) === LAPTOP);

        res = await login("emp1", "SuperSecret123", DESKTOP);
        check("a SECOND machine is still refused — this is the whole point",
            res.status === 409, `status ${res.status}`);
        check("and the first machine keeps working",
            (await api("GET", "/dashboard/me", { token: onLaptop })).status === 200);

        // A JWT's `iat` has one-second resolution, so two tokens minted for
        // the same payload inside the same second are byte-identical — and
        // the checks below would compare a token against itself.
        await new Promise((r) => setTimeout(r, 1100));
        res = await login("emp1", "SuperSecret123", LAPTOP);
        check("but the SAME machine can sign in again without logging out first",
            res.status === 200, `status ${res.status}`);
        const backOnLaptop = res.body.token;
        check("and it gets a fresh token", backOnLaptop !== onLaptop);
        check("while the old one is dead — one live session, still",
            (await api("GET", "/dashboard/me", { token: onLaptop })).status === 401);

        res = await login("emp1", "SuperSecret123", DESKTOP);
        check("the other machine is refused after that too",
            res.status === 409, `status ${res.status}`);

        // A session row written before this existed has no device to match.
        // Treated as a different machine, which is the safe direction.
        psql(DB, `UPDATE active_sessions SET device_id = NULL WHERE employee_id='E001'`);
        res = await login("emp1", "SuperSecret123", LAPTOP);
        check("a session from before this change is not matched to anything",
            res.status === 409, `status ${res.status}`);
        await api("POST", "/auth/logout", { token: backOnLaptop });
        res = await login("emp1", "SuperSecret123", LAPTOP);
        check("and one sign-out clears it for good",
            res.status === 200, `status ${res.status}`);
        await api("POST", "/auth/logout", { token: res.body.token });

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


        // ── refresh must not undo a force logout ────────────────────────
        //
        // THE WORST BUG FOUND IN THIS PRODUCT SO FAR, and it was silent.
        //
        // Force logout sets the stored token to NULL. Refresh only checked
        // that the presented JWT was cryptographically valid — which it still
        // is, for the rest of its twenty-four hours — and wrote a fresh token
        // straight back into the row. The client refreshes by itself, so an
        // administrator forced a logout, saw it work, and the app let itself
        // back in seconds later. Nothing anywhere said so.
        console.log("\nRefresh, after somebody has been signed out");

        psql(DB, `DELETE FROM active_sessions`);
        res = await login("emp1", "SuperSecret123", "machine-A");
        const live = res.body.token;
        check("a live session refreshes normally",
            (await api("POST", "/auth/refresh", { token: live })).status === 200);

        // A second's wait, deliberately. JWT records issue time to the
        // second, so a token re-signed inside the same second is byte for
        // byte the old one — the refresh is real, the string just has not
        // changed yet. Without this pause the check below would be testing
        // the clock rather than the rule.
        await new Promise((r) => setTimeout(r, 1100));
        const refreshed = (await api("POST", "/auth/refresh", { token: live })).body.token;
        check("a refresh really does issue a different token",
            Boolean(refreshed) && refreshed !== live, "same string came back");
        check("and the superseded one can no longer extend itself",
            (await api("POST", "/auth/refresh", { token: live })).status === 403,
            "an old token could still refresh — two live credentials for one session");

        psql(DB, `UPDATE active_sessions SET token = NULL WHERE employee_id = 'E001'`);
        res = await api("POST", "/auth/refresh", { token: refreshed });
        check("after a force logout, refresh is REFUSED",
            res.status === 403, `status ${res.status}`);
        check("and hands back no token at all",
            !res.body.token, JSON.stringify(res.body));
        check("with a message that sends them to the login screen",
            /log in again/i.test(res.body.message || ""), res.body.message);

        console.log("\nRefresh, for an account with no session row");
        //
        // `UPDATE ... WHERE employee_id` matches nothing when the row is
        // gone, so this used to hand back a working token recorded NOWHERE:
        // that account then had no session at all — reading as offline in the
        // panel while plainly working, with nothing left for the
        // one-machine rule to compare against.
        psql(DB, `DELETE FROM active_sessions`);
        res = await login("emp1", "SuperSecret123", "machine-A");
        const orphan = res.body.token;
        psql(DB, `DELETE FROM active_sessions`);
        res = await api("POST", "/auth/refresh", { token: orphan });
        check("refresh with no session row is refused",
            res.status === 403, `status ${res.status}`);
        check("and does not create a session out of nowhere",
            psql(DB, `SELECT COUNT(*) FROM active_sessions`) === "0",
            psql(DB, `SELECT COUNT(*) FROM active_sessions`));

        console.log("\nRefresh, for a suspended account");
        psql(DB, `DELETE FROM active_sessions`);
        res = await login("emp1", "SuperSecret123", "machine-A");
        const beforeSuspend = res.body.token;
        psql(DB, `UPDATE employees SET suspended = TRUE WHERE employee_id = 'E001'`);
        res = await api("POST", "/auth/refresh", { token: beforeSuspend });
        check("a suspended account is not handed a fresh credential",
            res.status === 403, `status ${res.status}`);
        check("and is told why", /suspended/i.test(res.body.message || ""), res.body.message);
        psql(DB, `UPDATE employees SET suspended = FALSE WHERE employee_id = 'E001'`);

        console.log("\nRefresh keeps the session looking alive");
        // Presence asks when the session was last heard from. A refresh is
        // the app saying it is running, so it has to count.
        psql(DB, `DELETE FROM active_sessions`);
        res = await login("emp1", "SuperSecret123", "machine-A");
        psql(DB, `UPDATE active_sessions SET last_seen = NOW() - INTERVAL '30 minutes'
                   WHERE employee_id = 'E001'`);
        await api("POST", "/auth/refresh", { token: res.body.token });
        check("refreshing stamps last_seen, so a working app is not read as gone",
            psql(DB, `SELECT (last_seen > NOW() - INTERVAL '1 minute')::text
                        FROM active_sessions WHERE employee_id = 'E001'`) === "true");

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
