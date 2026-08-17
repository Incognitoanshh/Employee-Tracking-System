/**
 * Closing or correcting a shift by hand.
 *
 * THE ONLY ENDPOINT IN ATTENDANCE THAT REWRITES HISTORY. Whatever it writes
 * into total_hours is what payroll pays on, so the interesting cases here are
 * not the ones that work — they are every attempt to get a wrong number in:
 * a time before the shift started, a time in the future, a mistyped year, an
 * admin reaching above their level, a change with no reason given.
 *
 * Run:  node server/tests/test_manual_checkout.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_checkout_${process.pid}`;
const PORT = 8000 + ((process.pid + 733) % 1000);
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

/**
 * A fresh open shift that started `hoursAgo` ago. Returns its id.
 *
 * FIRST LINE ONLY. psql prints the returned value AND its own "INSERT 0 1"
 * tag, so Number() of the whole thing is NaN — which then went into a query
 * as `WHERE id = NaN` and failed there rather than here.
 */
function insertedId(sql) {
    return Number(psql(sql).split("\n")[0].trim());
}

function openShift(employee, hoursAgo) {
    psql(`DELETE FROM attendance WHERE employee_id = '${employee}'`);
    return insertedId(
        `INSERT INTO attendance (employee_id, login_time)
         VALUES ('${employee}', (NOW() AT TIME ZONE 'UTC') - INTERVAL '${hoursAgo} hours')
         RETURNING id`);
}

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`Manual checkout (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(`INSERT INTO employees (employee_id, username, password, role, full_name) VALUES
            ('S001','owner','${hash}','super_admin','The Owner'),
            ('A001','admin1','${hash}','admin','Priya Nair'),
            ('E001','rajesh','${hash}','employee','Rajesh Kumar')`);

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

        const employee = await login("rajesh", "rajesh-laptop");
        const admin = await login("admin1", "admin-machine");
        const owner = await login("owner", "owner-machine");

        console.log("Closing a shift somebody forgot to close");
        let id = openShift("E001", 8);
        let res = await api("PATCH", `/attendance/${id}/checkout`,
            { token: admin, body: { reason: "Laptop died, confirmed with Rajesh" } });
        check("an administrator can close it", res.status === 200, JSON.stringify(res.body));
        check("the row is no longer open",
            Number(psql(`SELECT COUNT(*) FROM attendance
                          WHERE id = ${id} AND logout_time IS NULL`)) === 0);
        const hours = Number(psql(
            `SELECT round(EXTRACT(EPOCH FROM total_hours)/3600) FROM attendance WHERE id = ${id}`));
        check("and the hours are recomputed, not left stale", hours === 8, `${hours}h`);

        console.log("\nCorrecting a time that was already set");
        res = await api("PATCH", `/attendance/${id}/checkout`, {
            token: admin,
            body: { logout_time: istOffset(-3), reason: "He actually left at three" },
        });
        check("a closed shift can be corrected too", res.status === 200,
            JSON.stringify(res.body));
        const corrected = Number(psql(
            `SELECT round(EXTRACT(EPOCH FROM total_hours)/3600) FROM attendance WHERE id = ${id}`));
        check("and the total follows the new end time", corrected === 5, `${corrected}h`);

        console.log("\nEvery way of writing a wrong number in");
        id = openShift("E001", 8);
        res = await api("PATCH", `/attendance/${id}/checkout`,
            { token: admin, body: { logout_time: istOffset(-20), reason: "typo test" } });
        check("a time BEFORE the shift began is refused", res.status === 400,
            `HTTP ${res.status} — a negative shift would be paid as a negative day`);

        res = await api("PATCH", `/attendance/${id}/checkout`,
            { token: admin, body: { logout_time: istOffset(48), reason: "typo test" } });
        check("a time in the future is refused", res.status === 400, `HTTP ${res.status}`);

        res = await api("PATCH", `/attendance/${id}/checkout`,
            { token: admin, body: { logout_time: "20266-08-17 18:30", reason: "typo test" } });
        check("a mistyped year is refused rather than stored", res.status === 400,
            `HTTP ${res.status} — this is the one that makes an 80,000-hour shift`);
        check("and the shift is still open after all of that",
            Number(psql(`SELECT COUNT(*) FROM attendance
                          WHERE id = ${id} AND logout_time IS NULL`)) === 1,
            "a rejected edit must change nothing");

        console.log("\nWho may do it");
        res = await api("PATCH", `/attendance/${id}/checkout`,
            { token: employee, body: { reason: "closing my own shift" } });
        check("an employee cannot rewrite their own hours", res.status === 403,
            `HTTP ${res.status}`);

        const ownerShift = insertedId(
            `INSERT INTO attendance (employee_id, login_time)
             VALUES ('S001', (NOW() AT TIME ZONE 'UTC') - INTERVAL '3 hours')
             RETURNING id`);
        res = await api("PATCH", `/attendance/${ownerShift}/checkout`,
            { token: admin, body: { reason: "tidying up" } });
        check("an admin cannot rewrite a super admin's hours", res.status === 403,
            `HTTP ${res.status}`);
        res = await api("PATCH", `/attendance/${ownerShift}/checkout`,
            { token: owner, body: { reason: "closing my own forgotten shift" } });
        check("but the super admin can", res.status === 200, JSON.stringify(res.body));

        console.log("\nA reason is not optional");
        id = openShift("E001", 4);
        res = await api("PATCH", `/attendance/${id}/checkout`, { token: admin, body: {} });
        check("no reason is refused", res.status === 400, `HTTP ${res.status}`);
        res = await api("PATCH", `/attendance/${id}/checkout`,
            { token: admin, body: { reason: "  x " } });
        check("and neither is a token one", res.status === 400, `HTTP ${res.status}`);

        console.log("\nWhat the audit log remembers");
        id = openShift("E001", 6);
        await api("PATCH", `/attendance/${id}/checkout`,
            { token: admin, body: { reason: "Forgot to sign out on Friday" } });
        const entry = psql(
            `SELECT activity FROM activity_logs
              WHERE activity LIKE 'ATTENDANCE CHECKOUT SET%'
              ORDER BY id DESC LIMIT 1`);
        check("the change is written to the log", entry.length > 0);
        check("it names who made it", /by A001/.test(entry), entry);
        check("it carries the reason", /Forgot to sign out on Friday/.test(entry), entry);
        check("it says what the value WAS, not only what it became",
            /was still open/.test(entry), entry);

        // Correcting an already-set time must record the old timestamp — the
        // question asked later is never "what is it now", which is visible on
        // the page anyway.
        await api("PATCH", `/attendance/${id}/checkout`,
            { token: admin, body: { logout_time: istOffset(-2), reason: "Second correction" } });
        const second = psql(
            `SELECT activity FROM activity_logs
              WHERE activity LIKE 'ATTENDANCE CHECKOUT SET%'
              ORDER BY id DESC LIMIT 1`);
        check("a correction records the previous timestamp",
            /was \d{4}-\d{2}-\d{2}/.test(second), second);

        // The purge keeps administrative rows for the long period and drops
        // the noise. A prefix that is not registered is treated as noise, and
        // this is the record payroll questions are answered from months later.
        const { auditRowsSql } = require(
            path.join(root, "server", "utils", "audit_events"));
        const kept = Number(psql(
            `SELECT COUNT(*) FROM activity_logs WHERE ${auditRowsSql("activity")}
              AND activity LIKE 'ATTENDANCE CHECKOUT SET%'`));
        check("and it counts as evidence, so the short purge cannot delete it",
            kept >= 2, `${kept} rows matched the retained set`);

        console.log("\nA record that is not there");
        res = await api("PATCH", "/attendance/999999/checkout",
            { token: admin, body: { reason: "nothing here" } });
        check("gives a clean 404", res.status === 404, `HTTP ${res.status}`);
        res = await api("PATCH", "/attendance/not-a-number/checkout",
            { token: admin, body: { reason: "nothing here" } });
        check("and a nonsense id gives 400, not a crash", res.status === 400,
            `HTTP ${res.status}`);

        server.close();
        const pool = require(path.join(root, "server", "config", "db"));
        await pool.end();
    } finally {
        try { execFileSync("dropdb", ["--if-exists", DB]); } catch (_) {}
    }

    console.log(failures === 0
        ? "\nall manual checkout checks passed"
        : `\n${failures} FAILED`);
    process.exit(failures === 0 ? 0 : 1);
}

/** An IST timestamp string `hours` from now, as a person would type it. */
function istOffset(hours) {
    return execFileSync("psql", ["-d", DB, "-tAc",
        `SELECT to_char((NOW() AT TIME ZONE 'Asia/Kolkata') + INTERVAL '${hours} hours',
                        'YYYY-MM-DD HH24:MI')`],
        { encoding: "utf8" }).trim();
}

main().catch((error) => {
    console.error(error);
    try { execFileSync("dropdb", ["--if-exists", DB]); } catch (_) {}
    process.exit(1);
});
