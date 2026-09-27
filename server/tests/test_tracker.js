/**
 * The tracker board: everybody's state, in one answer.
 *
 * WHAT IT IS FOR. "Ek tracker window bnao... sare employee listed hongay aur
 * tracking chalega — working green, non working idle, aur count hoga kitne
 * der idle tha, red signal continuously working for 1 hr." Until now that
 * answer was eight clicks through eight pages, one person at a time.
 *
 * WHAT IS CHECKED, and each of these is a way a live board lies:
 *
 *   * OFFLINE IS NOT IDLE. Somebody whose app is closed is not sitting at a
 *     desk doing nothing, and a board that colours them the same amber says
 *     they are.
 *   * IDLE CARRIES ITS OWN CLOCK. "Idle" is worth little; "idle 23 minutes"
 *     is what somebody acts on.
 *   * AN UNBROKEN STRETCH IS MEASURED FROM WHERE IT STARTED, not from the
 *     last line the client wrote. Five ACTIVE lines in a row are one
 *     stretch of work, not five.
 *   * AND THE NUMBERS MATCH THE PERSON'S OWN PAGE, because they now come
 *     from the same arithmetic. Two screens disagreeing about the same
 *     morning is the argument nobody can settle.
 *
 * Run:  node server/tests/test_tracker.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_tracker_${process.pid}`;
const PORT = 8000 + ((process.pid + 419) % 1000);
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
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return { status: response.status, body: payload };
}

const login = async (u, device = "d1") =>
    (await api("POST", "/auth/login",
        { body: { username: u, password: PASSWORD, device_id: device } })).body.token;

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    let server;

    // ── the pure arithmetic first, against an invented morning ─────────
    const live = require(path.join(root, "server", "utils", "live_activity.js"));
    console.log("\nHow a morning adds up\n");

    const NINE = Date.UTC(2026, 7, 5, 9, 0, 0);
    const at = (minutes) => new Date(NINE + minutes * 60_000)
        .toISOString().slice(0, 19).replace("T", " ");

    let summary = live.summarise(
        [{ login_time: at(0), end_time: at(120) }],
        [{ created_at: at(30), activity: "USER IDLE (61.2s)" },
         { created_at: at(50), activity: "USER ACTIVE" }],
        NINE + 120 * 60_000);
    check("active is the time outside the idle stretches",
        live.formatDuration(summary.activeMs) === "01:40:00",
        live.formatDuration(summary.activeMs));
    check("and idle is the stretches themselves",
        live.formatDuration(summary.idleMs) === "00:20:00",
        live.formatDuration(summary.idleMs));
    // SOMEBODY WHO JUST SIGNED IN IS AT WORK. There is no event yet, and
    // calling that idle opens every morning with idle nobody was idle for.
    summary = live.summarise([{ login_time: at(0), end_time: at(10) }], [],
        NINE + 10 * 60_000);
    check("a fresh sign-in with no events yet is working, not idle",
        summary.state === "ACTIVE" && live.formatDuration(summary.activeMs) === "00:10:00",
        `${summary.state} / ${live.formatDuration(summary.activeMs)}`);
    check("and the stretch is counted from the sign-in itself",
        Math.round(summary.workingStreakMs / 60000) === 10,
        String(Math.round(summary.workingStreakMs / 60000)));

    // FIVE ACTIVE LINES ARE ONE STRETCH. The client writes one on every
    // wake; a board that restarted the clock at each would never show an
    // hour of unbroken work, which is the thing being looked for.
    summary = live.summarise(
        [{ login_time: at(0), end_time: at(90) }],
        [{ created_at: at(5), activity: "USER ACTIVE" },
         { created_at: at(20), activity: "USER ACTIVE" },
         { created_at: at(65), activity: "USER ACTIVE" }],
        NINE + 90 * 60_000);
    check("an unbroken stretch is measured from where it began",
        Math.round(summary.workingStreakMs / 60000) === 90,
        String(Math.round(summary.workingStreakMs / 60000)));

    summary = live.summarise(
        [{ login_time: at(0), end_time: at(60) }],
        [{ created_at: at(35), activity: "USER IDLE (60.4s)" }],
        NINE + 60 * 60_000);
    check("and somebody idle carries how long they have been idle",
        Math.round(summary.idleForMs / 60000) === 25,
        String(Math.round(summary.idleForMs / 60000)));
    check("with no working stretch to report while they are",
        summary.workingStreakMs === 0, String(summary.workingStreakMs));

    // ── then the board itself ──────────────────────────────────────────
    try {
        migrate(DB);
        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name, designation) VALUES
            ('SA01','owner','${hash}','super_admin','The Owner','Founder'),
            ('A001','admin1','${hash}','admin','Priya Nair','Manager'),
            ('E001','rajesh','${hash}','employee','Rajesh Kumar','Developer'),
            ('E002','sneha','${hash}','employee','Sneha Iyer','Designer'),
            ('E003','meera','${hash}','employee','Meera Rao','Analyst')`);

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

        ({ server } = require(path.join(root, "server", "server.js")));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        const admin = await login("admin1", "admin-mac");
        const rajesh = await login("rajesh", "rajesh-laptop");
        await api("POST", "/attendance/login", { token: rajesh, body: {} });
        const sneha = await login("sneha", "sneha-laptop");
        await api("POST", "/attendance/login", { token: sneha, body: {} });
        // Meera never signed in today at all.

        // Rajesh has been working without a break since he signed in; Sneha
        // went idle twenty minutes ago.
        psql(DB, `INSERT INTO activity_logs (employee_id, activity, created_at) VALUES
            ('E001','USER ACTIVE', (NOW() AT TIME ZONE 'UTC') - INTERVAL '70 minutes'),
            ('E001','USER ACTIVE', (NOW() AT TIME ZONE 'UTC') - INTERVAL '20 minutes'),
            ('E002','USER IDLE (61.0s)', (NOW() AT TIME ZONE 'UTC') - INTERVAL '20 minutes')`);
        // Backdate the shifts so there is something to measure.
        psql(DB, `UPDATE attendance
                     SET login_time = (NOW() AT TIME ZONE 'UTC') - INTERVAL '80 minutes'
                   WHERE employee_id IN ('E001','E002')`);

        console.log(`\nThe board (${DB})\n`);

        let res = await api("GET", "/admin/tracker", { token: rajesh });
        check("an employee cannot read the whole company's board",
            res.status === 403, `HTTP ${res.status}`);

        res = await api("GET", "/admin/tracker", { token: admin });
        check("an admin can", res.status === 200, `HTTP ${res.status}`);
        const board = res.body.data || [];
        const find = (id) => board.find((row) => row.employee_id === id) || {};

        check("everybody who is tracked is on it", board.length === 4,
            board.map((r) => r.employee_id).join(","));
        check("and the owner is not — they are not monitored",
            !board.some((r) => r.employee_id === "SA01"),
            board.map((r) => r.employee_id).join(","));

        check("somebody at work reads as working",
            find("E001").state === "ACTIVE", JSON.stringify(find("E001")));
        // THE RED SIGNAL THE BOARD IS FOR: an unbroken stretch, measured
        // from where it began rather than from the last line written.
        check("with the length of the stretch they are in",
            Math.abs(find("E001").working_seconds - 80 * 60) < 120,
            String(find("E001").working_seconds));

        check("somebody idle reads as idle", find("E002").state === "IDLE",
            JSON.stringify(find("E002")));
        check("and carries how long they have been",
            Math.abs(find("E002").idle_seconds - 20 * 60) < 120,
            String(find("E002").idle_seconds));
        check("with no working stretch while they are idle",
            find("E002").working_seconds === 0, String(find("E002").working_seconds));

        // OFFLINE IS NOT IDLE.
        check("somebody whose app is closed reads as offline, not idle",
            find("E003").state === "OFFLINE", JSON.stringify(find("E003")));

        check("the day's totals are there to read beside the state",
            /^\d\d:\d\d:\d\d$/.test(find("E001").active_today || ""),
            find("E001").active_today);

        // THE TWO SCREENS COME FROM THE SAME ARITHMETIC, and answer two
        // different questions with it: the board is today, the person's own
        // page is ninety days of attendance (see getEmployeeDetails). With
        // one day of data in the database they are identical, which is what
        // this used to assert — a check that would have gone on passing
        // while the page quietly answered something else.
        let details = await api("GET", "/admin/employee/E001", { token: admin });
        check("with one day on record, the page and the board agree to the second",
            details.body.data.active_time === find("E001").active_today,
            `${details.body.data.active_time} vs ${find("E001").active_today}`);
        check("idle as well",
            details.body.data.idle_time === find("E001").idle_today,
            `${details.body.data.idle_time} vs ${find("E001").idle_today}`);

        // AND A SHIFT FROM LAST WEEK SEPARATES THEM, the right way round.
        // Anybody reading "268:09:17" on the page beside "03:01:00" on the
        // board needs the page to be the longer of the two and the board to
        // be a possible day — this is what says which is which.
        psql(DB, `INSERT INTO attendance (employee_id, login_time, logout_time) VALUES
            ('E001', (NOW() AT TIME ZONE 'UTC') - INTERVAL '3 days',
                     (NOW() AT TIME ZONE 'UTC') - INTERVAL '3 days' + INTERVAL '8 hours')`);
        details = await api("GET", "/admin/employee/E001", { token: admin });
        const board2 = await api("GET", "/admin/tracker", { token: admin });
        const today = (board2.body.data || []).find((r) => r.employee_id === "E001") || {};
        const seconds = (text) => String(text || "0:0:0").split(":")
            .reduce((total, part) => total * 60 + Number(part), 0);
        check("a shift from last week lengthens the page's total",
            seconds(details.body.data.active_time) > seconds(today.active_today),
            `${details.body.data.active_time} vs ${today.active_today}`);
        check("and leaves today's figure on the board alone",
            today.active_today === find("E001").active_today,
            `${today.active_today} vs ${find("E001").active_today}`);
        check("which stays a length a day can actually be",
            seconds(today.active_today) <= 24 * 3600, today.active_today);
    } finally {
        if (server) server.close();
        try { require(path.join(root, "server", "config", "db")).end(); } catch (_) {}
        execFileSync("psql", ["-d", "postgres", "-c",
            `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`], { stdio: "pipe" });
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.exit(1);
    }
    console.log("all tracker checks passed");
    process.exit(0);
}

main();
