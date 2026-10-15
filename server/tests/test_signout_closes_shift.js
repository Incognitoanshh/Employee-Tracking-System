/**
 * Signing out closes the day's attendance row.
 *
 * THE BUG: every client called /auth/logout FIRST, which clears the token,
 * and only then sent /attendance/logout — with that dead token. It was
 * refused with a 401 nobody read, so every sign-out left the row open and the
 * attendance page said "Incomplete" beside somebody who had pressed Logout.
 * Reported by the owner from a real record (Shailabh, 07 Oct).
 *
 * Both halves are checked: the server now closes the shift inside
 * /auth/logout, so even the old client order (still on every installed
 * machine until it updates) ends in a closed row; and the new client order
 * works too. A stale token signing out must not close a newer shift.
 *
 * Run:  node server/tests/test_signout_closes_shift.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_signout_${process.pid}`;
const PORT = 8000 + ((process.pid + 419) % 1000);
const BASE = `http://127.0.0.1:${PORT}/api`;
const PASSWORD = "SuperSecret123";

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}

function psql(sql) {
    return execFileSync("psql", ["-d", DB, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
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

const openRows = (employee) => Number(psql(
    `SELECT COUNT(*) FROM attendance WHERE employee_id = '${employee}' AND logout_time IS NULL`));

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`Sign-out closes the shift (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(`INSERT INTO employees (employee_id, username, password, role, full_name) VALUES
            ('A001','admin1','${hash}','admin','Priya Nair'),
            ('E001','shailabh','${hash}','employee','Shailabh')`);

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

        const { server } = require(path.join(root, "server", "server.js"));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        console.log("The OLD client order — /auth/logout first, attendance second");
        let token = await login("shailabh", "laptop");
        let res = await api("POST", "/attendance/login", { token, body: {} });
        check("the shift opens", res.status === 200 && openRows("E001") === 1,
            JSON.stringify(res.body));
        res = await api("POST", "/auth/logout", { token });
        check("sign-out succeeds", res.status === 200);
        res = await api("POST", "/attendance/logout", { token, body: {} });
        check("the late /attendance/logout is still refused (token is dead)",
            res.status === 401, `HTTP ${res.status}`);
        check("BUT the row is closed anyway — by the sign-out itself",
            openRows("E001") === 0, `${openRows("E001")} open`);

        const admin = await login("admin1", "admin-machine");
        res = await api("GET", "/attendance/all?employee_id=E001", { token: admin });
        const row = (res.body.data || [])[0] || {};
        check("and the attendance page reads it as Completed, not Incomplete",
            row.attendance_status === "completed", JSON.stringify(row.attendance_status));
        check("with a check-out time and hours", Boolean(row.logout_time && row.total_hours));

        console.log("\nThe NEW client order — attendance first, then /auth/logout");
        token = await login("shailabh", "laptop");
        await api("POST", "/attendance/login", { token, body: {} });
        res = await api("POST", "/attendance/logout", { token, body: {} });
        check("/attendance/logout is accepted while the token lives",
            res.status === 200, `HTTP ${res.status}`);
        res = await api("POST", "/auth/logout", { token });
        check("and the sign-out after it still succeeds", res.status === 200);
        check("no row left open", openRows("E001") === 0);

        console.log("\nA stale token signing out must not end a newer shift");
        const stale = token;               // already signed out above
        token = await login("shailabh", "laptop");
        await api("POST", "/attendance/login", { token, body: {} });
        check("a new shift is open", openRows("E001") === 1);
        res = await api("POST", "/auth/logout", { token: stale });
        check("an old instance's sign-out is answered", res.status === 200);
        check("and the CURRENT shift is still open", openRows("E001") === 1,
            "a stale logout closed the session that replaced it");
        res = await api("POST", "/attendance/login", { token, body: {} });
        check("the current session is still valid", res.status === 200, `HTTP ${res.status}`);

        server.close();
        const pool = require(path.join(root, "server", "config", "db"));
        await pool.end();
    } finally {
        try { execFileSync("dropdb", ["--if-exists", DB]); } catch (_) {}
    }

    console.log(failures === 0
        ? "\nall sign-out checks passed"
        : `\n${failures} FAILED`);
    process.exit(failures === 0 ? 0 : 1);
}

main().catch((error) => {
    console.error(error);
    try { execFileSync("dropdb", ["--if-exists", DB]); } catch (_) {}
    process.exit(1);
});
