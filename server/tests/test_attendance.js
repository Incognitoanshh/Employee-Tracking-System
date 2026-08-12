/**
 * Starting a shift, when two of them arrive at once.
 *
 * TWO BUGS, BOTH FOUND BY RUNNING THIS RATHER THAN READING IT.
 *
 * 1. THE RACE. Closing the previous open row and opening a new one were two
 *    separate statements. Six logins arriving together interleaved: all six
 *    closed the old row, then all six inserted — and two rows were left open.
 *    An attendance row that never ends is a shift that never ends; it reaches
 *    the timesheet, the attendance report, and presence, where a row older
 *    than sixteen hours is treated as abandoned and the person reads Offline
 *    while plainly at work.
 *
 *    It is not a far-fetched race. The panel starts a shift on login and the
 *    auto-login path does the same, so a relaunch on a flaky connection is
 *    enough to fire two.
 *
 * 2. THE CATCH BLOCK THAT COULD NOT REPORT ANYTHING. Both handlers here ended
 *    with `Noneres.status(500)` — a typo that had been committed and was
 *    running in production. Whenever anything went wrong the catch itself
 *    threw a ReferenceError, so the server log said "Noneres is not defined"
 *    instead of what actually failed. Attendance is the one path where that
 *    mattered most, and it is exactly where a real fault was being hidden.
 *
 * Run:  node server/tests/test_attendance.js
 */
const { execFileSync } = require("child_process");
const util = require("util");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_attendance_${process.pid}`;
const PORT = 8000 + ((process.pid + 517) % 1000);
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

const openRows = (id) =>
    Number(psql(DB, `SELECT COUNT(*) FROM attendance
                      WHERE employee_id = '${id}' AND logout_time IS NULL`));

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`Attendance (${DB})\n`);

    // Anything the server writes while we drive it. The ReferenceError from
    // the broken catch surfaced here and nowhere else.
    const written = [];
    const realError = console.error;

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name) VALUES
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

        const { server, pool } = require(path.join(root, "server", "server.js"));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        console.log = ((real) => (...args) => real(...args))(console.log);
        // util.inspect, not String. The server logs the failure as an OBJECT,
        // and String({}) is "[object Object]" — the first version of this
        // check looked at that and saw nothing wrong, which is how a test
        // passes on code that is broken.
        console.error = (...args) => {
            written.push(args.map((a) => (typeof a === "string" ? a : util.inspect(a, { depth: 4 }))).join(" "));
            realError(...args);
        };

        const employee = await login("rajesh", "rajesh-laptop");
        const admin = await login("admin1", "admin-machine");

        console.log("One login");
        let res = await api("POST", "/attendance/login", { token: employee, body: {} });
        check("starts a shift", res.status === 200, `HTTP ${res.status}`);
        check("and exactly one row is open", openRows("E001") === 1, String(openRows("E001")));

        console.log("\nEight logins at the same instant, ten times over");
        // THE RACE. Eight at once is the shape of the bug, not an exotic
        // load: the panel and the auto-login path both start a shift, and a
        // reconnect can retry.
        //
        // TEN ROUNDS, because a race is a matter of timing and one round
        // proves nothing. The first version of this check ran a single round
        // and passed against the broken code — the interleave simply did not
        // happen that time. Ten rounds catches it every run measured.
        let worstOpen = 0;
        let roundsRun = 0;
        for (let round = 0; round < 10; round += 1) {
            psql(DB, `DELETE FROM attendance WHERE employee_id = 'E001'`);
            await Promise.all(
                Array.from({ length: 8 }, () =>
                    api("POST", "/attendance/login", { token: employee, body: {} })));
            worstOpen = Math.max(worstOpen, openRows("E001"));
            roundsRun += 1;
        }
        check(`ten rounds leave exactly ONE row open every time`,
            worstOpen === 1,
            `one round left ${worstOpen} open — every extra one is a shift that never ends`);
        const total = Number(psql(DB,
            `SELECT COUNT(*) FROM attendance WHERE employee_id = 'E001'`));
        check("and the ones they replaced were closed, not deleted",
            total === 8, `${total} rows from the last round`);

        console.log("\nA shift left open by a crash");
        // Nothing closes a row when an app is killed. The next login must,
        // or presence starts calling a working employee abandoned.
        psql(DB, `DELETE FROM attendance WHERE employee_id = 'E001'`);
        psql(DB, `INSERT INTO attendance (employee_id, login_time)
                  VALUES ('E001', (NOW() AT TIME ZONE 'UTC') - INTERVAL '4 days')`);
        await api("POST", "/attendance/login", { token: employee, body: {} });
        check("is closed by the next login", openRows("E001") === 1, String(openRows("E001")));
        const stale = Number(psql(DB,
            `SELECT COUNT(*) FROM attendance
              WHERE employee_id = 'E001' AND logout_time IS NULL
                AND login_time < (NOW() AT TIME ZONE 'UTC') - INTERVAL '1 day'`));
        check("and the four-day-old one is not the one still open",
            stale === 0, `${stale} ancient rows still open`);
        check("so the employee list calls them online again",
            (((await api("GET", "/admin/employees", { token: admin })).body.data || [])
                .find((r) => r.employee_id === "E001") || {}).status === "online",
            "Offline beside a Last Seen of 'just now' is what this looked like");

        console.log("\nSigning in again after the app was closed at lunchtime");
        // The other half of the same bug, and the one that bites every day
        // rather than every few days: close the app at ten, sign in at six,
        // and the row in between used to be stamped with the moment of the
        // new login — eight hours on the timesheet for one hour of work.
        psql(DB, `DELETE FROM attendance WHERE employee_id = 'E001'`);
        psql(DB, `DELETE FROM activity_logs WHERE employee_id = 'E001'`);
        psql(DB, `DELETE FROM active_sessions WHERE employee_id = 'E001'`);
        psql(DB, `INSERT INTO attendance (employee_id, login_time)
                  VALUES ('E001', (NOW() AT TIME ZONE 'UTC') - INTERVAL '8 hours')`);
        psql(DB, `INSERT INTO activity_logs (employee_id, activity, created_at)
                  VALUES ('E001','KEYBOARD',
                          (NOW() AT TIME ZONE 'UTC') - INTERVAL '7 hours')`);
        const again = await login("rajesh", "rajesh-laptop");
        await api("POST", "/attendance/login", { token: again, body: {} });
        const lunch = Number(psql(DB,
            `SELECT round(EXTRACT(EPOCH FROM total_hours)/3600)
               FROM attendance
              WHERE employee_id = 'E001' AND logout_time IS NOT NULL
              ORDER BY id DESC LIMIT 1`));
        check("the closed shift records the hour actually worked, not the eight since",
            lunch === 1,
            `${lunch} hours recorded — 8 is the whole gap, which nobody worked`);

        console.log("\nA shift nobody ever closed");
        // THE 94-HOUR SHIFT. Closing the app without signing out leaves the
        // row open, and it used to stay open until that person's next login —
        // which then recorded everything in between as one shift. A real row
        // in the customer's database read 94:38:22 for exactly this reason,
        // and it goes into the timesheet.
        //
        // It also made two screens disagree: Attendance said ACTIVE, because
        // the rule there is "no logout_time", while the employee list said
        // Offline, because presence treats a row older than a full shift as
        // abandoned.
        const { closeAbandonedShifts } = require(
            path.join(root, "server", "utils", "attendance_cleanup"));

        psql(DB, `DELETE FROM attendance WHERE employee_id = 'E001'`);
        psql(DB, `DELETE FROM activity_logs WHERE employee_id = 'E001'`);
        psql(DB, `DELETE FROM active_sessions WHERE employee_id = 'E001'`);
        // Signed in two days ago, last heard from two hours after that.
        psql(DB, `INSERT INTO attendance (employee_id, login_time)
                  VALUES ('E001', (NOW() AT TIME ZONE 'UTC') - INTERVAL '2 days')`);
        psql(DB, `INSERT INTO activity_logs (employee_id, activity, created_at)
                  VALUES ('E001','KEYBOARD',
                          (NOW() AT TIME ZONE 'UTC') - INTERVAL '2 days' + INTERVAL '2 hours')`);

        let closed = await closeAbandonedShifts(pool);
        check("the abandoned shift is closed", closed === 1, `${closed} closed`);
        const hours = Number(psql(DB,
            `SELECT round(EXTRACT(EPOCH FROM total_hours)/3600)
               FROM attendance WHERE employee_id = 'E001'`));
        check("at the last moment there was evidence of them, not at 'now'",
            hours === 2,
            `${hours} hours recorded — 48 would mean it counted the days nobody worked`);

        // Somebody still at work must never be signed out by this, however
        // long their shift has run.
        psql(DB, `DELETE FROM attendance WHERE employee_id = 'E001'`);
        psql(DB, `INSERT INTO attendance (employee_id, login_time)
                  VALUES ('E001', (NOW() AT TIME ZONE 'UTC') - INTERVAL '20 hours')`);
        psql(DB, `INSERT INTO active_sessions (employee_id, token, login_time, last_seen)
                  VALUES ('E001','live-token', NOW(), NOW())`);
        closed = await closeAbandonedShifts(pool);
        check("a long shift with a LIVE session is left alone", closed === 0,
            `${closed} closed — an employee at work was signed out`);

        // And a shift that simply started this morning is nobody's business.
        psql(DB, `DELETE FROM attendance WHERE employee_id = 'E001'`);
        psql(DB, `DELETE FROM active_sessions WHERE employee_id = 'E001'`);
        psql(DB, `INSERT INTO attendance (employee_id, login_time)
                  VALUES ('E001', (NOW() AT TIME ZONE 'UTC') - INTERVAL '3 hours')`);
        closed = await closeAbandonedShifts(pool);
        check("and a shift from this morning is untouched", closed === 0,
            `${closed} closed`);

        console.log("\nWhen the write itself fails");
        // THE BROKEN CATCH. employee_id is VARCHAR(50); 300 characters cannot
        // be stored, so the insert fails and the error path runs — which is
        // the point. It must answer the caller and say what went wrong,
        // rather than throwing a second error out of the handler.
        written.length = 0;
        res = await api("POST", "/attendance/login",
            { token: admin, body: { employee_id: "X".repeat(300) } });
        check("the caller gets a clean 500, not a hang", res.status === 500,
            `HTTP ${res.status}`);
        check("with a message rather than a stack trace",
            typeof res.body.message === "string" && !/\bat \//.test(res.body.message || ""),
            JSON.stringify(res.body).slice(0, 120));
        check("and the REAL error reaches the log, not a ReferenceError from the catch",
            !written.some((line) => /is not defined/.test(line)),
            written.filter((l) => /is not defined/.test(l)).join(" ").slice(0, 160) ||
            "the catch block threw before it could report anything");
        check("the server is still serving afterwards",
            (await api("GET", "/admin/employees", { token: admin })).status === 200);

        server.close();
        await pool.end();
    } finally {
        console.error = realError;
        try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.stdout.write("", () => process.exit(1));
    } else {
        console.log("all attendance checks passed");
        process.stdout.write("", () => process.exit(0));
    }
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
