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
        // AND THEY LEAVE ONE ROW ALTOGETHER, not eight.
        //
        // This check used to require exactly 8 — one row per call, seven of
        // them closed the instant they opened. That was the behaviour, so the
        // test agreed with it, and the customer's own attendance list showed
        // what it produced: one morning split into four and five pieces of
        // half an hour each, with 00:00:00 rows between them.
        //
        // A shift that is still alive is now RESUMED, so eight simultaneous
        // starts are one shift, which is what eight simultaneous starts
        // actually are.
        const total = Number(psql(DB,
            `SELECT COUNT(*) FROM attendance WHERE employee_id = 'E001'`));
        check("and the eight calls leave ONE shift, not eight fragments",
            total === 1, `${total} rows from the last round`);
        const empty = Number(psql(DB,
            `SELECT COUNT(*) FROM attendance
              WHERE employee_id = 'E001' AND total_hours < INTERVAL '1 second'`));
        check("with no zero-length rows left behind", empty === 0,
            `${empty} rows of 00:00:00 — the litter this used to make`);

        console.log("\nSigning in twice while plainly still at work");
        // THE LINE BETWEEN RESUMING AND STARTING AGAIN, tested from both
        // sides, because getting it wrong in either direction is a real cost:
        // resume too eagerly and the gap somebody was away is billed as work;
        // resume too rarely and every day fragments into unreadable pieces.
        psql(DB, `DELETE FROM attendance WHERE employee_id = 'E001'`);
        psql(DB, `DELETE FROM activity_logs WHERE employee_id = 'E001'`);
        const first = await api("POST", "/attendance/login",
            { token: employee, body: {} });
        psql(DB, `INSERT INTO activity_logs (employee_id, activity, created_at)
                  VALUES ('E001','KEYBOARD', (NOW() AT TIME ZONE 'UTC'))`);
        const second = await api("POST", "/attendance/login",
            { token: employee, body: {} });
        check("the second start returns the SAME shift",
            second.body.id === first.body.id,
            `${first.body.id} then ${second.body.id} — a new id is a new row`);
        check("and says so, so the client is not guessing",
            second.body.resumed === true, util.inspect(second.body));
        check("one row, still open",
            Number(psql(DB, `SELECT COUNT(*) FROM attendance
                              WHERE employee_id = 'E001'`)) === 1);

        // The other side: away long enough that the shift is genuinely over.
        psql(DB, `DELETE FROM attendance WHERE employee_id = 'E001'`);
        psql(DB, `DELETE FROM activity_logs WHERE employee_id = 'E001'`);
        psql(DB, `INSERT INTO attendance (employee_id, login_time)
                  VALUES ('E001', (NOW() AT TIME ZONE 'UTC') - INTERVAL '6 hours')`);
        psql(DB, `INSERT INTO activity_logs (employee_id, activity, created_at)
                  VALUES ('E001','KEYBOARD',
                          (NOW() AT TIME ZONE 'UTC') - INTERVAL '5 hours')`);
        const afterGap = await api("POST", "/attendance/login",
            { token: employee, body: {} });
        check("a shift gone quiet for hours is NOT resumed",
            afterGap.body.resumed !== true, util.inspect(afterGap.body));
        const billed = Number(psql(DB,
            `SELECT round(EXTRACT(EPOCH FROM total_hours)/3600)
               FROM attendance
              WHERE employee_id = 'E001' AND logout_time IS NOT NULL
              ORDER BY id DESC LIMIT 1`));
        check("and it is closed at the last evidence, not at this login",
            billed === 1,
            `${billed} hours — 6 would be the whole gap, which nobody worked`);

        console.log("\nFiltering by the record's state");
        // The filter runs in SQL, so the total and the page numbers describe
        // the rows actually shown. Filtering after the fetch would page over
        // the unfiltered set — "Page 1, Total: 33" above four visible rows.
        psql(DB, `DELETE FROM attendance WHERE employee_id = 'E001'`);
        // THE HEARTBEAT IS MOVED, THE SESSION ROW IS LEFT ALONE.
        //
        // The first version of this deleted the session and inserted one with
        // a made-up token — which invalidated the employee's real token, so
        // every later request came back 401 "Logged in from another device"
        // and two checks thirty lines below failed for a reason that had
        // nothing to do with them. Aging last_seen tests the same thing and
        // breaks nothing.
        psql(DB, `UPDATE active_sessions SET last_seen = NOW() - INTERVAL '1 hour'
                   WHERE employee_id = 'E001'`);
        psql(DB, `INSERT INTO attendance (employee_id, login_time, logout_time, total_hours)
                  VALUES ('E001', (NOW() AT TIME ZONE 'UTC') - INTERVAL '9 hours',
                                  (NOW() AT TIME ZONE 'UTC') - INTERVAL '1 hour',
                                  INTERVAL '8 hours')`);
        psql(DB, `INSERT INTO attendance (employee_id, login_time)
                  VALUES ('E001', (NOW() AT TIME ZONE 'UTC') - INTERVAL '30 hours')`);

        const byState = async (state) => {
            const r = await api("GET",
                `/attendance/all?employee_id=E001${state ? `&status=${state}` : ""}`,
                { token: admin });
            return { rows: (r.body.data || []).length, total: r.body.total };
        };

        let all = await byState("");
        check("both rows are there unfiltered", all.rows === 2, JSON.stringify(all));
        let done = await byState("completed");
        check("completed finds only the closed one", done.rows === 1, JSON.stringify(done));
        check("and the TOTAL follows the filter, so paging is honest",
            done.total === 1, `total said ${done.total} for 1 row`);
        let abandoned = await byState("incomplete");
        check("not-signed-out finds the abandoned one", abandoned.rows === 1,
            JSON.stringify(abandoned));
        let live = await byState("active");
        check("active finds neither — no live session", live.rows === 0,
            JSON.stringify(live));

        psql(DB, `UPDATE active_sessions SET last_seen = NOW() WHERE employee_id = 'E001'`);
        live = await byState("active");
        check("and finds it once the heartbeat is there", live.rows === 1,
            JSON.stringify(live));
        abandoned = await byState("incomplete");
        check("which stops it being counted as abandoned", abandoned.rows === 0,
            JSON.stringify(abandoned));
        const junk = await byState("'; DROP TABLE attendance; --");
        check("an unknown filter value is ignored, not interpolated",
            junk.rows === 2,
            "anything else here would be a way into the query");
        check("and the table is still there",
            Number(psql(DB, `SELECT COUNT(*) FROM attendance`)) >= 2);


        console.log("\nSearching by name, and over a range of days");
        // A NAME IS WHAT PEOPLE KNOW. The box matched an exact employee id
        // only, so an administrator who knew somebody as "Rajesh" had to
        // leave the page, find the id, and come back.
        psql(DB, `DELETE FROM attendance WHERE employee_id = 'E001'`);
        psql(DB, `INSERT INTO attendance (employee_id, login_time, logout_time)
                  VALUES ('E001','2026-07-02 04:00:00','2026-07-02 12:00:00'),
                         ('E001','2026-07-10 04:00:00','2026-07-10 12:00:00'),
                         ('E001','2026-08-01 04:00:00','2026-08-01 12:00:00')`);

        const search = async (query) =>
            ((await api("GET", `/attendance/all?${query}`, { token: admin }))
                .body.total);

        check("a full name finds the rows", await search("employee_id=Rajesh") === 3,
            String(await search("employee_id=Rajesh")));
        check("so does part of it, in any case",
            await search("employee_id=raj") === 3);
        check("and the id still works exactly",
            await search("employee_id=E001") === 3);
        check("a name nobody has finds nothing rather than everything",
            await search("employee_id=zzz") === 0,
            "an unmatched filter that returns the whole table is worse than "
            + "an empty page");

        // THE RANGE. One day at a time made "last week" seven searches.
        check("a range covers both its ends",
            await search("from=2026-07-01&to=2026-07-31&employee_id=E001") === 2,
            String(await search("from=2026-07-01&to=2026-07-31&employee_id=E001")));
        check("the ends are inclusive",
            await search("from=2026-07-02&to=2026-07-02&employee_id=E001") === 1);
        check("an open start means everything up to a day",
            await search("to=2026-07-05&employee_id=E001") === 1);
        check("an open end means everything since one",
            await search("from=2026-07-05&employee_id=E001") === 2);
        check("and the total follows the range, so paging stays honest",
            await search("from=2026-08-01&to=2026-08-01&employee_id=E001") === 1);

        console.log("\nYesterday's shift does not become today's");
        // REPORTED FROM THE RUNNING APP. An admin's panel was left open
        // overnight, so activity kept arriving; the shift opened at 18:19 was
        // still open the next morning and the page read "Active, 15:39:18".
        // One row across two days, and fifteen hours that payroll would have
        // treated as worked.
        //
        // Resuming was judged on recent evidence alone. A shift belongs to
        // the day it started.
        psql(DB, `DELETE FROM attendance WHERE employee_id = 'E001'`);
        psql(DB, `DELETE FROM activity_logs WHERE employee_id = 'E001'`);
        // Opened yesterday evening, with activity a minute ago — the exact
        // shape of an app that was never closed.
        psql(DB, `INSERT INTO attendance (employee_id, login_time)
                  VALUES ('E001', ((NOW() AT TIME ZONE 'Asia/Kolkata')::date
                                   - INTERVAL '1 day' + INTERVAL '18 hours')
                                  AT TIME ZONE 'Asia/Kolkata')`);
        psql(DB, `INSERT INTO activity_logs (employee_id, activity, created_at)
                  VALUES ('E001','KEYBOARD',
                          (NOW() AT TIME ZONE 'UTC') - INTERVAL '1 minute')`);

        const overnight = await api("POST", "/attendance/login",
            { token: employee, body: {} });
        check("it is NOT resumed", overnight.body.resumed !== true,
            util.inspect(overnight.body));
        // AND IT IS NOT CLOSED AT THIS MORNING'S ACTIVITY EITHER.
        //
        // Reported from the running app: the shift was closed correctly, at
        // the newest evidence — which was 11:13 the NEXT day, because the
        // panel had been left running overnight writing idle and active
        // rows. It recorded 15:56:17, and that goes to payroll as worked.
        // Tomorrow cannot say when somebody stopped yesterday.
        const overnightHours = Number(psql(DB,
            `SELECT COALESCE(round(EXTRACT(EPOCH FROM total_hours)/3600), 0)
               FROM attendance
              WHERE employee_id = 'E001' AND logout_time IS NOT NULL
              ORDER BY id DESC LIMIT 1`));
        check("and yesterday's shift is not billed into this morning",
            overnightHours <= 6,
            `${overnightHours} hours recorded for an evening shift`);
        check("yesterday's row is closed",
            Number(psql(DB, `SELECT COUNT(*) FROM attendance
                              WHERE employee_id = 'E001'
                                AND logout_time IS NULL`)) === 1,
            "exactly one open row, and it must be today's");
        const today = psql(DB,
            `SELECT to_char((login_time AT TIME ZONE 'UTC')
                            AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD')
               FROM attendance
              WHERE employee_id = 'E001' AND logout_time IS NULL`);
        const nowDay = psql(DB,
            `SELECT to_char(NOW() AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD')`);
        check("and the open one started today", today === nowDay,
            `${today} vs ${nowDay}`);
        const spanned = Number(psql(DB,
            `SELECT COUNT(*) FROM attendance
              WHERE employee_id = 'E001' AND total_hours > INTERVAL '14 hours'`));
        check("no row spans the night", spanned === 0,
            `${spanned} row(s) longer than fourteen hours`);

        // CLEAN UP AFTER THIS BLOCK. The minute-old activity row above is
        // what makes the shift look alive, and the force-logout checks below
        // close a shift AT ITS LAST EVIDENCE — they would find this one and
        // close two-day-old shifts at "now". A section that leaves state
        // behind breaks a section thirty lines away, which is the hardest
        // kind of failure to read.
        psql(DB, `DELETE FROM activity_logs WHERE employee_id = 'E001'`);
        psql(DB, `DELETE FROM attendance WHERE employee_id = 'E001'`);

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

        console.log("\nAN OPEN ROW IS ONLY 'ACTIVE' IF SOMEBODY IS ACTUALLY THERE");
        // Seen on the live panel with two accounts at once: Attendance said
        // ACTIVE, the employee list said "Offline · 11 hr ago", about the
        // same person at the same moment. The column drew ACTIVE from
        // logout_time being null and asked nothing else, so an app closed
        // without signing out read as somebody at their desk for up to
        // sixteen hours — until the abandoned-shift sweep reached it.
        psql(DB, `DELETE FROM attendance`);
        psql(DB, `DELETE FROM active_sessions`);
        const working = await login("rajesh", "at-their-desk");
        psql(DB, `INSERT INTO attendance (employee_id, login_time)
                  VALUES ('E001', (NOW() AT TIME ZONE 'UTC') - INTERVAL '1 hour')`);

        let list = await api("GET", "/attendance/all", { token: admin });
        let mine = (list.body.data || []).find((r) => r.employee_id === "E001");
        check("a live session reads as live", mine && mine.session_live === true,
            JSON.stringify(mine));

        // The app is closed: the heartbeat stops. Nothing else changes — the
        // row is still open, exactly as it was.
        psql(DB, `UPDATE active_sessions SET last_seen = NOW() - INTERVAL '30 minutes'
                   WHERE employee_id='E001'`);
        list = await api("GET", "/attendance/all", { token: admin });
        mine = (list.body.data || []).find((r) => r.employee_id === "E001");
        check("once the heartbeat stops it does NOT", mine && mine.session_live === false,
            JSON.stringify(mine));
        check("and the row is still open — this is a label, not a closure",
            mine && mine.logout_time === null, JSON.stringify(mine && mine.logout_time));

        // THE TWO SCREENS MUST AGREE. That is the whole point.
        const listed = await api("GET", "/admin/employees", { token: admin });
        const asShown = (listed.body.data || []).find((r) => r.employee_id === "E001");
        check("and the employee list says the same thing",
            asShown && asShown.status === "offline" && mine.session_live === false,
            `employees: ${asShown && asShown.status}, attendance: ${mine && mine.session_live}`);

        console.log("\nA FORCE LOGOUT ENDS THE SHIFT, NOT JUST THE SESSION");
        // Reported from the live panel: an employee was force-logged-out and
        // their app closed, and Attendance went on showing ACTIVE.
        //
        // The row is normally closed by the client as it shuts down, by posting
        // to /attendance/logout. After a force logout that post carries a token
        // the server has just invalidated, so it is refused — and nothing else
        // closes the row until the sixteen-hour sweep. Two screens disagreeing
        // about the same person again: Attendance said working, the employee
        // list said offline.
        psql(DB, `DELETE FROM active_sessions`);
        psql(DB, `DELETE FROM attendance`);
        const victim = await login("rajesh", "victim-machine");
        // The row is written directly rather than through /attendance/login,
        // which stamps the server's own clock — this test needs a shift that
        // began hours ago, with evidence in the middle of it.
        psql(DB, `DELETE FROM attendance`);
        psql(DB, `INSERT INTO attendance (employee_id, login_time)
                  VALUES ('E001','2026-08-16 03:00:00')`);
        check("their shift is open", psql(DB,
            `SELECT COUNT(*) FROM attendance
              WHERE employee_id='E001' AND logout_time IS NULL`) === "1");

        // Some evidence of being there, an hour after signing in — and a
        // session that went quiet after it. This is the reported case: the
        // app was CLOSED, and only then did the administrator press the
        // button. The heartbeat counts as evidence too, so it has to be old
        // for the activity line to be the last thing known.
        // THE SLATE IS CLEARED FIRST, and this is not tidiness.
        //
        // An earlier block leaves an activity row at NOW - 2 days. This block
        // pins its own evidence to a FIXED date, 2026-08-16, and asserts the
        // shift closes there. The two only collide when "two days ago"
        // happens to BE 2026-08-16 — so this test passed every day except
        // one, and failed on that day for a reason nothing in it explains.
        //
        // Found while chasing a different bug, on the one day it could
        // appear.
        psql(DB, `DELETE FROM activity_logs WHERE employee_id = 'E001'`);
        psql(DB, `INSERT INTO activity_logs (employee_id, activity, created_at)
                  VALUES ('E001','USER ACTIVE','2026-08-16 04:00:00')`);
        psql(DB, `UPDATE active_sessions SET last_seen = '2026-08-16 03:30:00+00'
                   WHERE employee_id = 'E001'`);

        let forced = await api("POST", "/admin/force-logout",
            { token: admin, body: { employee_id: "E001" } });
        check("the force logout is accepted", forced.status === 200,
            `HTTP ${forced.status} ${JSON.stringify(forced.body).slice(0, 120)}`);
        check("and it says the shift was closed too",
            /shift was closed/.test(forced.body.message || ""), forced.body.message);
        check("the row is no longer open", psql(DB,
            `SELECT COUNT(*) FROM attendance
              WHERE employee_id='E001' AND logout_time IS NULL`) === "0",
            "Attendance would still read ACTIVE for somebody who was signed out");
        check("closed at the last evidence, not at the moment the button was pressed",
            psql(DB, `SELECT TO_CHAR(logout_time, 'YYYY-MM-DD HH24:MI:SS')
                        FROM attendance WHERE employee_id='E001'`) === "2026-08-16 04:00:00",
            psql(DB, `SELECT logout_time::text FROM attendance WHERE employee_id='E001'`));

        // WHEN THE APP IS STILL RUNNING, "last evidence" is now — the person
        // was at their desk until the moment they were signed out, and that
        // is what should be recorded.
        psql(DB, `DELETE FROM attendance`);
        psql(DB, `INSERT INTO attendance (employee_id, login_time)
                  VALUES ('E001', (NOW() AT TIME ZONE 'UTC') - INTERVAL '2 hours')`);
        psql(DB, `UPDATE active_sessions SET last_seen = NOW() WHERE employee_id='E001'`);
        await api("POST", "/admin/force-logout",
            { token: admin, body: { employee_id: "E001" } });
        check("a live session closes at about now, not at the login time",
            psql(DB, `SELECT (logout_time > (NOW() AT TIME ZONE 'UTC') - INTERVAL '2 minutes')::text
                        FROM attendance WHERE employee_id='E001'`) === "true",
            psql(DB, `SELECT logout_time::text FROM attendance WHERE employee_id='E001'`));

        // AND IT DOES NOT REACH ANYBODY ELSE'S ROW.
        psql(DB, `DELETE FROM attendance`);
        psql(DB, `INSERT INTO attendance (employee_id, login_time) VALUES
            ('E001','2026-08-16 03:00:00'), ('A001','2026-08-16 03:00:00')`);
        await api("POST", "/admin/force-logout",
            { token: admin, body: { employee_id: "E001" } });
        check("the other person's shift is untouched", psql(DB,
            `SELECT COUNT(*) FROM attendance
              WHERE employee_id='A001' AND logout_time IS NULL`) === "1");

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
