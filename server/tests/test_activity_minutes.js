/**
 * The minutes a client scored, and the alert they can add up to.
 *
 * WHY THE SERVER KEEPS THE PARTS AND NOT JUST THE SCORE. An alert reading
 * "activity score 12" gives an administrator nothing they can act on, and
 * nothing anybody could use to show the alert was wrong. The keystrokes, the
 * clicks and the movements are what make "no keystroke in 45 of the last 60
 * minutes" sayable — and this is a guess about somebody's working day, so
 * being able to show it wrong matters more than being able to raise it.
 *
 * WHAT IS CHECKED:
 *
 *   * a batch is stored, for the CALLER — there is no field here that could
 *     be pointed at another employee;
 *   * a batch sent twice does not double the day. A client that uploaded
 *     successfully and then lost the connection before marking the rows
 *     sent will send them again, and it must not turn one hour into two;
 *   * rubbish is refused rather than stored as zeroes;
 *   * and the alert appears only with the evidence for it — long enough,
 *     and with no keystroke anywhere in the window.
 *
 * Run:  node server/tests/test_activity_minutes.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_actmin_${process.pid}`;
const PORT = 8000 + ((process.pid + 277) % 1000);
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

/** A minute as the client reports it. */
function minute(offsetMinutes, extra = {}) {
    const when = new Date(Date.now() - offsetMinutes * 60_000);
    const stamp = when.toISOString().slice(0, 16).replace("T", " ");
    return {
        minute: stamp, score: 12, band: "IDLE",
        keystrokes: 0, clicks: 0, scrolls: 0, mouse_moves: 58,
        window_changes: 0, automation_suspected: true,
        reasons: "input, identical intervals, repetitive movement",
        ...extra,
    };
}

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    let server;
    try {
        migrate(DB);
        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name) VALUES
            ('A001','admin1','${hash}','admin','Priya Nair'),
            ('E001','rajesh','${hash}','employee','Rajesh Kumar'),
            ('E002','sneha','${hash}','employee','Sneha Iyer')`);
        // The global config row already exists from the schema; this only
        // makes the day a working one so the other rules stay quiet and this
        // file is about one alert rather than all of them.
        psql(DB, `UPDATE employee_configs SET shift_start = '09:00:00', weekly_offs = ''
                   WHERE employee_id IS NULL`);

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

        console.log(`\nStoring what a client scored (${DB})\n`);

        let res = await api("POST", "/logs/activity-minutes",
            { token: rajesh, body: { minutes: [minute(3), minute(2), minute(1)] } });
        check("a batch is accepted", res.status === 200 && res.body.stored === 3,
            `HTTP ${res.status} ${JSON.stringify(res.body)}`);
        check("and stored under the employee who sent it",
            psql(DB, `SELECT COUNT(*) FROM activity_minutes WHERE employee_id='E001'`) === "3",
            psql(DB, `SELECT COUNT(*) FROM activity_minutes`));
        check("with the parts, not just the score",
            psql(DB, `SELECT mouse_moves || '/' || keystrokes FROM activity_minutes
                       WHERE employee_id='E001' LIMIT 1`) === "58/0",
            psql(DB, `SELECT mouse_moves || '/' || keystrokes FROM activity_minutes LIMIT 1`));

        // THE RETRY THAT MUST NOT DOUBLE THE DAY.
        res = await api("POST", "/logs/activity-minutes",
            { token: rajesh, body: { minutes: [minute(3), minute(2), minute(1)] } });
        check("the same minutes sent again do not become six",
            psql(DB, `SELECT COUNT(*) FROM activity_minutes WHERE employee_id='E001'`) === "3",
            psql(DB, `SELECT COUNT(*) FROM activity_minutes WHERE employee_id='E001'`));

        for (const [what, body] of [
            ["an empty batch", { minutes: [] }],
            ["no batch at all", {}],
            ["a minute that is not a time", { minutes: [minute(1, { minute: "yesterday" })] }],
            ["a score above a hundred", { minutes: [minute(1, { score: 900 })] }],
            ["a score below zero", { minutes: [minute(1, { score: -5 })] }],
        ]) {
            res = await api("POST", "/logs/activity-minutes", { token: rajesh, body });
            check(`${what} is refused`, res.status === 400, `HTTP ${res.status}`);
        }

        res = await api("POST", "/logs/activity-minutes", { body: { minutes: [minute(1)] } });
        check("and without a token, nothing is stored at all",
            res.status === 401, `HTTP ${res.status}`);

        console.log("\nThe alert it can add up to");

        res = await api("GET", "/admin/alerts", { token: admin });
        const automated = (list) => (list || []).filter((a) => a.type === "AUTOMATED_INPUT");
        check("three suspicious minutes are not an accusation",
            automated(res.body.alerts).length === 0,
            JSON.stringify((res.body.alerts || []).map((a) => a.type)));

        // Three quarters of an hour of it, which is the default threshold.
        const many = [];
        for (let i = 4; i <= 48; i += 1) many.push(minute(i));
        await api("POST", "/logs/activity-minutes", { token: rajesh, body: { minutes: many } });

        res = await api("GET", "/admin/alerts", { token: admin });
        const raised = automated(res.body.alerts);
        check("three quarters of an hour is", raised.length === 1,
            JSON.stringify((res.body.alerts || []).map((a) => a.type)));
        check("and it names the person and what was seen",
            raised.length === 1 && raised[0].employee_id === "E001"
            && /no keystroke/.test(raised[0].detail), JSON.stringify(raised[0] || {}));

        // ONE KEYSTROKE, ANYWHERE. A machine moving a mouse produces none.
        await api("POST", "/logs/activity-minutes",
            { token: rajesh, body: { minutes: [minute(5, { keystrokes: 1, automation_suspected: false })] } });
        res = await api("GET", "/admin/alerts", { token: admin });
        check("one keystroke in the hour ends it",
            automated(res.body.alerts).length === 0,
            JSON.stringify((res.body.alerts || []).map((a) => a.type)));

        // And nobody else is swept up in it.
        check("and it was never about anybody else",
            automated(res.body.alerts).every((a) => a.employee_id !== "E002"),
            JSON.stringify(automated(res.body.alerts)));
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
    console.log("all activity minute checks passed");
    process.exit(0);
}

main();
