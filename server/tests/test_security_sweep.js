/**
 * A hostile pass over the whole API surface.
 *
 * Every other suite here tests a feature working. This one tries to break in:
 * it holds an ordinary employee's token and walks every route asking for
 * things that token has no business getting.
 *
 * WHY IT IS A SWEEP RATHER THAN A LIST
 * Access control is not written in one place. It lives in route middleware
 * for some endpoints and inside the controller for others, and the two look
 * identical from the outside. A route added without either guard is invisible
 * until somebody tries it. So the point of this file is to try all of them,
 * every push, forever.
 *
 * The routes are enumerated FROM THE SOURCE, not typed out here. A list typed
 * by hand goes stale the moment somebody adds an endpoint — which is exactly
 * when this check matters most.
 *
 * Run:  node server/tests/test_security_sweep.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const { migrate } = require("./_migrate");

const DB = `ets_sec_${process.pid}`;
const PORT = 8000 + ((process.pid + 457) % 1000);
const BASE = `http://127.0.0.1:${PORT}/api`;
const PASSWORD = "SuperSecret123";

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
        ...(body && method !== "GET" ? { body: JSON.stringify(body) } : {}),
    });
    let payload = {};
    let text = "";
    try {
        text = await response.text();
        payload = JSON.parse(text);
    } catch (_) {}
    return { status: response.status, body: payload, text };
}

const login = async (u, device = "d") =>
    (await api("POST", "/auth/login",
        { body: { username: u, password: PASSWORD, device_id: device } })).body.token;

/**
 * Every route the server actually mounts, read out of the route files.
 *
 * Returns [method, path, mountedUnder]. Path params are filled with a value
 * that exists, so a 404 means "not found" rather than "bad id".
 */
function discoverRoutes() {
    const dir = path.join(__dirname, "..", "routes");
    const mounts = {
        "admin.routes.js": "/admin",
        "attendance.routes.js": "/attendance",
        "chat.routes.js": "/chat",
        "config.routes.js": "/config",
        "dashboard.routes.js": "/dashboard",
        "log.routes.js": "/logs",
        "screenshot.routes.js": "/screenshots",
        // /auth belongs here too. It was left out of the first version of
        // this sweep because login and refresh are meant to be anonymous —
        // but /auth/password sits beside them and changes a password, so
        // excluding the whole mount created exactly the blind spot this file
        // exists to prevent. The three that must stay open are skipped by
        // name below instead.
        "auth.routes.js": "/auth",
    };
    const found = [];
    for (const [file, mount] of Object.entries(mounts)) {
        const src = fs.readFileSync(path.join(dir, file), "utf8");
        const re = /router\.(get|post|put|patch|delete)\(\s*(?:\/\/[^\n]*\n\s*)?["']([^"']+)["']/g;
        let m;
        while ((m = re.exec(src)) !== null) {
            found.push([m[1].toUpperCase(), mount + m[2]]);
        }
    }
    return found;
}

function fillParams(route, ids) {
    return route
        .replace(":employee_id", ids.employee)
        .replace(":holiday_date", "2026-01-01")
        .replace(":seq", String(ids.seq))
        .replace(":id", String(ids.id));
}

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`Security sweep (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name) VALUES
            ('SA001','superadmin','${hash}','super_admin','Owner'),
            ('A001','admin1','${hash}','admin','Priya Nair'),
            ('E001','rajesh','${hash}','employee','Rajesh Kumar'),
            ('E002','amit','${hash}','employee','Amit Sharma')`);

        Object.assign(process.env, {
            DB_HOST: process.env.PGHOST || "127.0.0.1",
            DB_PORT: process.env.PGPORT || "5432",
            DB_NAME: DB,
            DB_USER: process.env.PGUSER || process.env.USER,
            DB_PASSWORD: process.env.PGPASSWORD || "unused-locally",
            JWT_SECRET: "test-secret-not-used-in-production",
            PORT: String(PORT),
            ENCRYPTION_KEY: "0".repeat(64),
        });

        const { server, pool } = require(path.join(root, "server", "server.js"));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        const sa = await login("superadmin", "sa");
        const admin = await login("admin1", "adm");
        const victim = await login("amit", "victim");
        const attacker = await login("rajesh", "attacker");

        // Something for each side to own, so IDOR has a real target.
        psql(DB, `INSERT INTO screenshots (employee_id, file_name)
                  VALUES ('E002','victim-secret.enc') RETURNING id`);
        const victimShot = psql(DB,
            `SELECT id FROM screenshots WHERE employee_id='E002' LIMIT 1`);
        psql(DB, `INSERT INTO activity_logs (employee_id, activity)
                  VALUES ('E002','VICTIM PRIVATE ACTIVITY')`);

        const ids = { employee: "E002", id: victimShot, seq: 1 };

        // ── 1. every route needs a token ────────────────────────────────
        console.log("Nothing works without a token");
        const routes = discoverRoutes();
        check("the sweep found the API surface, rather than an empty list",
            routes.length >= 40, `${routes.length} routes discovered`);

        const openToAnonymous = [];
        for (const [method, route] of routes) {
            if (route === "/auth/login" || route === "/auth/refresh"
                || route === "/auth/logout") continue;
            const res = await api(method, fillParams(route, ids), { body: {} });
            if (res.status !== 401 && res.status !== 403) {
                openToAnonymous.push(`${method} ${route} -> ${res.status}`);
            }
        }
        check("every route refuses an anonymous caller",
            openToAnonymous.length === 0, openToAnonymous.join("; "));

        // ── 2. an employee cannot reach admin routes ────────────────────
        console.log("\nAn ordinary employee, knocking on every admin door");
        const reachable = [];
        for (const [method, route] of routes) {
            if (!route.startsWith("/admin")) continue;
            const res = await api(method, fillParams(route, ids),
                { token: attacker, body: {} });
            // 400/404/409 mean the guard let them through and the handler
            // then disliked the request — still a failure of access control.
            if (res.status !== 403 && res.status !== 401) {
                reachable.push(`${method} ${route} -> ${res.status}`);
            }
        }
        check("no admin route is reachable by an employee",
            reachable.length === 0, reachable.join("; "));

        // ── 3. reading other people's data ──────────────────────────────
        console.log("\nReading somebody else's records");

        let res = await api("GET", `/screenshots/download/${victimShot}`, { token: attacker });
        check("another employee's screenshot cannot be downloaded",
            res.status === 403, `status ${res.status}`);

        res = await api("GET", "/screenshots/all", { token: attacker });
        let leaked = JSON.stringify(res.body).includes("victim-secret");
        check("the screenshot list does not include other people's",
            !leaked, "an employee could see everyone's screenshots");

        res = await api("GET", "/logs/all", { token: attacker });
        leaked = JSON.stringify(res.body).includes("VICTIM PRIVATE ACTIVITY");
        check("the activity log does not include other people's",
            !leaked, "an employee could read everyone's activity");

        res = await api("GET", "/attendance/all", { token: attacker });
        leaked = JSON.stringify(res.body).includes("E002");
        check("attendance does not include other people's",
            !leaked, "an employee could read everyone's attendance");

        // ── 4. writing as somebody else ─────────────────────────────────
        console.log("\nWriting under somebody else's name");

        await api("POST", "/logs/create",
            { token: attacker, body: { employee_id: "E002", activity: "FORGED BY ATTACKER" } });
        check("an employee cannot write an activity log as another",
            psql(DB, `SELECT COUNT(*) FROM activity_logs
                       WHERE employee_id='E002' AND activity='FORGED BY ATTACKER'`) === "0",
            "a forged entry landed under the victim's name");

        await api("POST", "/attendance/login",
            { token: attacker, body: { employee_id: "E002" } });
        check("an employee cannot clock in as another",
            psql(DB, `SELECT COUNT(*) FROM attendance WHERE employee_id='E002'`) === "0",
            "attendance was created for somebody else");

        res = await api("POST", "/logs/idle-daily",
            { token: attacker, body: { employee_id: "E002", day: "2026-08-01", idle_seconds: 99999 } });
        check("an employee cannot record idle time against another",
            psql(DB, `SELECT COUNT(*) FROM idle_daily WHERE employee_id='E002'`) === "0",
            "idle time was written against somebody else");

        // ── 5. privilege escalation ─────────────────────────────────────
        console.log("\nTrying to become an administrator");

        res = await api("POST", "/admin/employees/E001/role",
            { token: attacker, body: { role: "super_admin" } });
        check("an employee cannot promote themselves",
            res.status === 403 || res.status === 401, `status ${res.status}`);
        check("and the role really is unchanged",
            psql(DB, `SELECT role FROM employees WHERE employee_id='E001'`) === "employee");

        res = await api("POST", "/admin/employees/A001/role",
            { token: admin, body: { role: "super_admin" } });
        check("an ordinary admin cannot make anybody a super admin",
            res.status === 403, `status ${res.status}`);

        res = await api("POST", "/admin/employees",
            { token: admin, body: {
                employee_id: "X001", username: "sneaky", password: "SuperSecret123",
                role: "super_admin", full_name: "Sneaky" } });
        check("nor create one",
            res.status === 403, `status ${res.status}`);
        check("and no such account exists",
            psql(DB, `SELECT COUNT(*) FROM employees WHERE employee_id='X001'`) === "0");

        // ── 6. path traversal ───────────────────────────────────────────
        console.log("\nAsking for files outside the store");

        psql(DB, `INSERT INTO screenshots (employee_id, file_name)
                  VALUES ('E001','../../../../etc/passwd')`);
        const evil = psql(DB,
            `SELECT id FROM screenshots WHERE file_name LIKE '%passwd%'`);
        res = await api("GET", `/screenshots/download/${evil}`, { token: attacker });
        check("a traversing file name in the database does not escape the store",
            res.status !== 200 || !res.text.includes("root:"),
            "the server served a file from outside the upload directory");

        for (const attempt of ["../../server/.env", "..%2f..%2fserver%2f.env", "....//....//etc/passwd"]) {
            res = await api("GET", `/chat/attachments/${encodeURIComponent(attempt)}`,
                { token: attacker });
            check(`an attachment id of "${attempt}" is refused`,
                res.status !== 200, `status ${res.status}`);
        }

        // ── 7. SQL injection ────────────────────────────────────────────
        console.log("\nSQL in the places that take text");

        const payloads = [
            "' OR '1'='1", "'; DROP TABLE employees; --", "\\'; SELECT pg_sleep(3); --",
            "%' UNION SELECT NULL,NULL,NULL --",
        ];
        for (const payload of payloads) {
            const q = encodeURIComponent(payload);
            const before = psql(DB, `SELECT COUNT(*) FROM employees`);
            await api("GET", `/admin/employees?search=${q}`, { token: sa });
            await api("GET", `/chat/search?q=${q}`, { token: attacker });
            await api("GET", `/logs/all?search=${q}`, { token: sa });
            const after = psql(DB, `SELECT COUNT(*) FROM employees`);
            check(`"${payload.slice(0, 22)}…" changes nothing`, before === after,
                `employees went from ${before} to ${after}`);
        }
        check("the employees table is still there",
            Number(psql(DB, `SELECT COUNT(*) FROM employees`)) >= 4);

        // ── 8. a token that should no longer work ───────────────────────
        console.log("\nTokens past their welcome");

        const doomed = await login("amit", "throwaway");
        await api("POST", "/auth/logout", { token: doomed });
        res = await api("GET", "/chat/me/teams", { token: doomed });
        check("a token stops working after logging out",
            res.status === 401 || res.status === 403, `status ${res.status}`);

        const jwt = require(path.join(root, "server", "node_modules", "jsonwebtoken"));
        const forged = jwt.sign({ employee_id: "E001", role: "super_admin" },
            "not-the-real-secret", { expiresIn: "1h" });
        res = await api("GET", "/admin/employees", { token: forged });
        check("a token signed with the wrong secret is refused",
            res.status === 403 || res.status === 401, `status ${res.status}`);

        const selfPromoted = jwt.sign({ employee_id: "E001", role: "super_admin" },
            process.env.JWT_SECRET, { expiresIn: "1h" });
        res = await api("GET", "/admin/employees", { token: selfPromoted });
        check("a validly signed token for a session that does not exist is refused",
            res.status === 403 || res.status === 401,
            `status ${res.status} — role came from the token, not the database`);

        const expired = jwt.sign({ employee_id: "E001", role: "employee" },
            process.env.JWT_SECRET, { expiresIn: "-1h" });
        res = await api("GET", "/chat/me/teams", { token: expired });
        check("an expired token is refused", res.status === 403, `status ${res.status}`);

        // ── 9. what the server says about itself ────────────────────────
        console.log("\nWhat leaks in an error");

        res = await api("GET", "/nope/not/a/route");
        check("an unknown route does not name the database or a file path",
            !/postgres|\/Users\/|\/home\/|node_modules/i.test(res.text), res.text.slice(0, 120));

        res = await api("POST", "/auth/login", { body: { username: "rajesh" } });
        check("a bad login does not say whether the account exists",
            !/no such user|not found/i.test(res.body.message || ""), res.body.message);

        res = await api("POST", "/auth/login",
            { body: { username: "rajesh", password: "wrong-password" } });
        check("a wrong password says the same thing as a wrong username",
            !/password is|incorrect password/i.test(res.body.message || ""), res.body.message);

        // ── 10. brute force ─────────────────────────────────────────────
        console.log("\nGuessing a password over and over");
        let blocked = false;
        for (let i = 0; i < 15; i += 1) {
            const attempt = await api("POST", "/auth/login",
                { body: { username: "amit", password: `guess-${i}` } });
            if (attempt.status === 429) { blocked = true; break; }
        }
        check("repeated failures are rate limited", blocked,
            "fifteen wrong passwords in a row went unchallenged");

        server.close();
        await pool.end();
    } finally {
        try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.stdout.write("", () => process.exit(1));
    } else {
        console.log("all security sweep checks passed");
        process.stdout.write("", () => process.exit(0));
    }
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
