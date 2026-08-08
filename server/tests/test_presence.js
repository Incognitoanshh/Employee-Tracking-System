/**
 * Who is online — the one claim this whole product is for.
 *
 * THE BUG THIS WAS WRITTEN FOR
 * Online meant "has an attendance row with no logout_time". That records
 * having STARTED work; it is not evidence of anything still running. So every
 * way an app can end without a clean logout — a crash, a closed lid, a force
 * logout, a password reset clearing the token, being signed out by a login
 * from another machine — left a green Online dot for up to sixteen hours.
 *
 * It was spotted by eye, in the panel: an employee sitting on Online with no
 * row in active_sessions at all. My own audit had not caught it, because I
 * had checked security, dependencies and deployment and never once asked the
 * product its central question and compared the answer with the database.
 *
 * That is what these do. Each one sets up a real state in the tables and asks
 * the API, rather than reasoning about the SQL.
 *
 * Run:  node server/tests/test_presence.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_presence_${process.pid}`;
const PORT = 8000 + ((process.pid + 311) % 1000);
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
    try { payload = await response.json(); } catch (_) {}
    return { status: response.status, body: payload };
}

const login = async (u, device = "d1") =>
    (await api("POST", "/auth/login",
        { body: { username: u, password: PASSWORD, device_id: device } })).body.token;

async function statusOf(token, id) {
    const res = await api("GET", "/admin/employees", { token });
    const rows = res.body.data || res.body.employees || [];
    return (rows.find((r) => r.employee_id === id) || {}).status;
}

async function onlineCount(token) {
    const res = await api("GET", "/dashboard/summary", { token });
    return Number((res.body.data || {}).online_employees);
}

/** An open shift, started `hoursAgo` ago. */
function openShift(id, hoursAgo = 1) {
    psql(DB, `INSERT INTO attendance (employee_id, login_time)
              VALUES ('${id}', (NOW() AT TIME ZONE 'UTC') - INTERVAL '${hoursAgo} hours')`);
}

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`Presence (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name) VALUES
            ('SA001','superadmin','${hash}','super_admin','Owner'),
            ('E001','rajesh','${hash}','employee','Rajesh Kumar'),
            ('E002','amit','${hash}','employee','Amit Sharma'),
            ('E003','sneha','${hash}','employee','Sneha Iyer')`);

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

        const sa = await login("superadmin", "owner-machine");

        console.log("Somebody actually working");
        const e1 = await login("rajesh", "rajesh-laptop");
        openShift("E001");
        // One authenticated call, which is what stamps the heartbeat.
        await api("GET", "/chat/me/teams", { token: e1 });
        check("shows online", (await statusOf(sa, "E001")) === "online",
            await statusOf(sa, "E001"));
        check("and the dashboard counts them", (await onlineCount(sa)) === 1,
            String(await onlineCount(sa)));

        console.log("\nThe app stopped without logging out");
        // THE BUG. The shift row stays open — nothing closes it — but the
        // session is gone. This is a crash, a closed lid, a force logout, or
        // a password reset. It used to read Online for sixteen hours.
        psql(DB, `DELETE FROM active_sessions WHERE employee_id = 'E001'`);
        check("is NOT online — an open shift row is not a running app",
            (await statusOf(sa, "E001")) === "offline",
            await statusOf(sa, "E001"));
        check("and the dashboard agrees, on the same screen",
            (await onlineCount(sa)) === 0, String(await onlineCount(sa)));

        console.log("\nThe session went quiet");
        const back = await login("rajesh", "rajesh-laptop");
        await api("GET", "/chat/me/teams", { token: back });
        psql(DB, `UPDATE active_sessions
                     SET last_seen = NOW() - INTERVAL '4 minutes'
                   WHERE employee_id = 'E001'`);
        check("four minutes of silence is still online — the poll backs off that far",
            (await statusOf(sa, "E001")) === "online", await statusOf(sa, "E001"));

        psql(DB, `UPDATE active_sessions
                     SET last_seen = NOW() - INTERVAL '20 minutes'
                   WHERE employee_id = 'E001'`);
        check("twenty minutes is not",
            (await statusOf(sa, "E001")) === "offline", await statusOf(sa, "E001"));

        console.log("\nA token cleared by a password reset");
        psql(DB, `UPDATE active_sessions
                     SET last_seen = NOW(), token = NULL
                   WHERE employee_id = 'E001'`);
        check("a session with no token is not a session",
            (await statusOf(sa, "E001")) === "offline", await statusOf(sa, "E001"));

        console.log("\nA shift nobody ever closed");
        // Sixteen hours is longer than any real shift; past that the row is
        // abandoned, not open. Kept as a backstop for a machine left on.
        psql(DB, `DELETE FROM attendance WHERE employee_id = 'E002'`);
        openShift("E002", 20);
        const e2 = await login("amit", "amit-pc");
        await api("GET", "/chat/me/teams", { token: e2 });
        check("a twenty-hour-old shift row does not count, even with a live app",
            (await statusOf(sa, "E002")) === "offline", await statusOf(sa, "E002"));

        console.log("\nThe app is running but the shift is over");
        psql(DB, `UPDATE attendance SET logout_time = (NOW() AT TIME ZONE 'UTC')
                   WHERE employee_id = 'E002' AND logout_time IS NULL`);
        await api("GET", "/chat/me/teams", { token: e2 });
        check("clocked out is offline, whatever is sitting in the tray",
            (await statusOf(sa, "E002")) === "offline", await statusOf(sa, "E002"));

        console.log("\nSomebody who has never started");
        check("is offline, not missing", (await statusOf(sa, "E003")) === "offline",
            await statusOf(sa, "E003"));

        console.log("\nThe two screens must never disagree");
        // They did. The employee list and the dashboard each had their own
        // copy of this rule, so one could show a person online while the
        // other counted them offline.
        psql(DB, `DELETE FROM attendance`);
        psql(DB, `DELETE FROM active_sessions WHERE employee_id <> 'SA001'`);
        for (const [user, id, device] of [
            ["rajesh", "E001", "r1"], ["amit", "E002", "a1"], ["sneha", "E003", "s1"]]) {
            const token = await login(user, device);
            openShift(id);
            await api("GET", "/chat/me/teams", { token });
        }
        const listed = (await api("GET", "/admin/employees", { token: sa }))
            .body.data.filter((r) => r.status === "online").length;
        check("the list and the count are the same number",
            listed === (await onlineCount(sa)),
            `list says ${listed}, dashboard says ${await onlineCount(sa)}`);
        check("and it is three", listed === 3, String(listed));

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
        console.log("all presence checks passed");
        process.stdout.write("", () => process.exit(0));
    }
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
