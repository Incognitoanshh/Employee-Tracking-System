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

/**
 * A minute as the client reports it — ON THE EMPLOYEE'S OWN CLOCK.
 *
 * The client stamps these with now_ist(), not UTC (see the migration). This
 * built them in UTC, which is five and a half hours away from what a real
 * client sends — so every check here was about minutes no client ever
 * writes, and the window they fall in could not be wrong.
 */
const IST_MINUS_UTC_MS = (5 * 60 + 30) * 60_000;

function minute(offsetMinutes, extra = {}) {
    const when = new Date(Date.now() + IST_MINUS_UTC_MS - offsetMinutes * 60_000);
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

        // BUILT ONCE, SENT TWICE. Built twice, a minute boundary can fall
        // between the two calls and the "same" batch is a different one —
        // which is a flaky test rather than a broken upsert.
        const first = [minute(3), minute(2), minute(1)];
        let res = await api("POST", "/logs/activity-minutes",
            { token: rajesh, body: { minutes: first } });
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
            { token: rajesh, body: { minutes: first } });
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

        // ── ONE MINUTE TWICE IN ONE BATCH ─────────────────────────────────
        //
        // A client whose clock steps backwards writes the same stamp twice.
        // This used to make Postgres refuse the whole statement, so the
        // employee got a 500, kept the batch, and retried it on every tick —
        // from then on nothing about that person's activity ever arrived
        // again, with nothing to show that anything had stopped.
        const twice = minute(9, { score: 30, reasons: "first" });
        res = await api("POST", "/logs/activity-minutes", {
            token: rajesh,
            body: { minutes: [twice, { ...twice, score: 44, reasons: "corrected" }] },
        });
        check("the same minute twice in one batch is accepted, not refused",
            res.status === 200, `HTTP ${res.status}`);
        check("and the later copy is the one kept",
            psql(DB, `SELECT score || '|' || reasons FROM activity_minutes
                       WHERE employee_id='E001' AND minute='${twice.minute}'`) === "44|corrected",
            psql(DB, `SELECT score || '|' || reasons FROM activity_minutes
                       WHERE employee_id='E001' AND minute='${twice.minute}'`));
        check("one row, not two",
            psql(DB, `SELECT COUNT(*) FROM activity_minutes
                       WHERE employee_id='E001' AND minute='${twice.minute}'`) === "1");

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

        // ── AN AFTERNOON IS NOT "THE LAST HOUR" ───────────────────────────
        //
        // The rule's own sentence is "30 of the last 60 minutes", and the
        // window was measured in UTC against minutes written in IST: five
        // and a half hours of slack, in which a jiggler that ran before
        // lunch was still being reported as running now. Sneha's minutes are
        // three hours old and must count for nothing.
        console.log("\nAnd the hour it is about is really an hour");

        const sneha = await login("sneha", "sneha-laptop");
        const longAgo = [];
        for (let i = 0; i <= 44; i += 1) longAgo.push(minute(180 + i));
        res = await api("POST", "/logs/activity-minutes",
            { token: sneha, body: { minutes: longAgo } });
        check("minutes from three hours ago are stored", res.status === 200,
            `HTTP ${res.status}`);

        res = await api("GET", "/admin/alerts", { token: admin });
        check("but they are not what somebody is doing now",
            automated(res.body.alerts).every((a) => a.employee_id !== "E002"),
            JSON.stringify(automated(res.body.alerts)));

        // WHILE THE SAME EVIDENCE INSIDE THE HOUR DOES SPEAK. Otherwise the
        // check above would pass just as well on a rule that never fires.
        const justNow = [];
        for (let i = 1; i <= 45; i += 1) justNow.push(minute(i));
        await api("POST", "/logs/activity-minutes",
            { token: sneha, body: { minutes: justNow } });
        res = await api("GET", "/admin/alerts", { token: admin });
        const hers = automated(res.body.alerts).filter((a) => a.employee_id === "E002");
        check("the same evidence inside the hour does", hers.length === 1,
            JSON.stringify(automated(res.body.alerts)));
        check("and it is described as the hour it actually covers",
            hers.length === 1 && /of the last (4[5-9]|5\d|60) minutes/.test(hers[0].detail),
            (hers[0] || {}).detail);
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
