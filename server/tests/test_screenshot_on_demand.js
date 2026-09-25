/**
 * A screenshot an administrator asks for.
 *
 * THE REQUEST. "Agar employee online and working hai to button click and
 * uska current screenshot aa jaye." The schedule already takes pictures at
 * moments nobody chooses; this is the other half — somebody is on a call
 * about what is on that screen right now.
 *
 * WHY IT IS THREE STEPS AND NOT ONE. Nothing here can call the client: it
 * polls /api/config/sync every five seconds. So the click writes a request
 * down, the next sync carries it, and the upload that follows names the
 * request it answers. What this file checks is that those three steps hold
 * together — and, just as much, that they do not pretend:
 *
 *   * a request is only made for somebody who is ONLINE, because a picture
 *     from whenever the app next opened, under a button that says "now",
 *     is hours of difference that nothing on the screen would show;
 *   * handing the request over is not the same as taking the picture, so
 *     the sync does not close it — only an upload does;
 *   * a request nobody answered expires instead of waiting for ever.
 *
 * AND IT IS ON THE RECORD. An administrator looking at somebody's screen on
 * demand is an act they may have to answer for, so it is written where
 * administrative acts are kept, with the name of whoever pressed the button.
 *
 * Run:  node server/tests/test_screenshot_on_demand.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_ssreq_${process.pid}`;
const PORT = 8000 + ((process.pid + 511) % 1000);
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

/** A capture, uploaded the way the client uploads one. */
async function upload(token, requestId) {
    const form = new FormData();
    form.append("screenshot",
        new Blob([Buffer.from("not-a-real-encrypted-capture")],
            { type: "application/octet-stream" }), "shot.enc");
    if (requestId !== undefined) form.append("request_id", String(requestId));
    const response = await fetch(`${BASE}/screenshots/upload`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: form,
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return { status: response.status, body: payload };
}

const login = async (u, device = "d1") =>
    (await api("POST", "/auth/login",
        { body: { username: u, password: PASSWORD, device_id: device } })).body.token;

const sync = async (token, employee_id, device_id) =>
    api("POST", "/config/sync", { token, body: { employee_id, device_id } });

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    const uploads = require("fs").mkdtempSync(
        path.join(require("os").tmpdir(), "ets_ssreq_"));
    let server;

    try {
        migrate(DB);
        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name) VALUES
            ('SA01','owner','${hash}','super_admin','The Owner'),
            ('A001','admin1','${hash}','admin','Priya Nair'),
            ('E001','rajesh','${hash}','employee','Rajesh Kumar'),
            ('E002','sneha','${hash}','employee','Sneha Iyer')`);

        Object.assign(process.env, {
            DB_HOST: process.env.PGHOST || "127.0.0.1",
            DB_PORT: process.env.PGPORT || "5432",
            DB_NAME: DB,
            DB_USER: process.env.PGUSER || process.env.USER,
            DB_PASSWORD: process.env.PGPASSWORD || "unused-locally",
            JWT_SECRET: "test-secret-not-used-in-production",
            PORT: String(PORT),
            ENCRYPTION_KEY: "0".repeat(64),
            UPLOAD_DIR: uploads,
        });

        ({ server } = require(path.join(root, "server", "server.js")));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        const admin = await login("admin1", "admin-mac");
        const rajesh = await login("rajesh", "rajesh-laptop");
        // Signed in, and at work: an employee is online when their session is
        // live AND they have a shift open today — presence.js decides this,
        // and this test uses the real answer rather than a second opinion.
        await api("POST", "/attendance/login", { token: rajesh, body: {} });
        const sneha = await login("sneha", "sneha-laptop");

        console.log(`\nWho a screenshot can be asked for (${DB})\n`);

        let res = await api("POST", "/admin/employees/E002/screenshot", { token: admin });
        check("somebody signed in but not at work is refused", res.status === 409,
            `HTTP ${res.status}`);
        check("and told why, by name", /sneha|Sneha/i.test(res.body.message || ""),
            res.body.message);

        res = await api("POST", "/admin/employees/SA01/screenshot", { token: admin });
        check("the owner's screen cannot be asked for — they are not monitored",
            res.status === 403, `HTTP ${res.status}`);

        res = await api("POST", "/admin/employees/E001/screenshot", { token: rajesh });
        check("an employee cannot ask for anybody's screen", res.status === 403,
            `HTTP ${res.status}`);

        res = await api("POST", "/admin/employees/NOBODY/screenshot", { token: admin });
        check("nor can one be asked for somebody who does not exist",
            res.status === 404, `HTTP ${res.status}`);

        console.log("\nThe request, and the poll that collects it");

        res = await api("POST", "/admin/employees/E001/screenshot", { token: admin });
        check("an admin may ask for an employee who is online", res.status === 200,
            `HTTP ${res.status} ${JSON.stringify(res.body).slice(0, 90)}`);
        const requestId = res.body.request_id;
        check("and gets the request back to follow", Boolean(requestId), String(requestId));

        // TWO CLICKS ARE ONE REQUEST. A slow network must not take two
        // pictures a second apart and leave one of them pending for ever.
        res = await api("POST", "/admin/employees/E001/screenshot", { token: admin });
        check("clicking again joins the request already waiting",
            String(res.body.request_id) === String(requestId) && res.body.already_waiting === true,
            `${res.body.request_id} vs ${requestId}`);

        const audit = psql(DB,
            `SELECT activity FROM activity_logs WHERE employee_id='E001'
              AND activity LIKE 'SCREENSHOT REQUESTED%' ORDER BY id DESC LIMIT 1`);
        check("it is on the record, with who asked", audit.includes("A001"), audit || "(nothing)");

        let synced = await sync(rajesh, "E001", "rajesh-laptop");
        check("the employee's next sync carries it",
            String(synced.body.config?.capture_now) === String(requestId),
            String(synced.body.config?.capture_now));

        // HANDED OVER IS NOT TAKEN. The app may be closing; the screen may
        // refuse. Only a picture closes the request.
        check("but the request is still waiting, because no picture exists yet",
            psql(DB, `SELECT status FROM screenshot_requests WHERE id=${requestId}`) === "PENDING",
            psql(DB, `SELECT status FROM screenshot_requests WHERE id=${requestId}`));

        // Nobody else's sync is affected by it.
        const other = await sync(sneha, "E002", "sneha-laptop");
        check("and nobody else is asked for a picture they were not asked for",
            !other.body.config?.capture_now, String(other.body.config?.capture_now));

        console.log("\nThe picture that answers it");

        const sent = await upload(rajesh, requestId);
        check("the capture uploads", sent.status === 200, `HTTP ${sent.status}`);
        const row = psql(DB,
            `SELECT status || '|' || COALESCE(screenshot_id::text,'-')
               FROM screenshot_requests WHERE id=${requestId}`);
        check("the request is closed by it", row.startsWith("TAKEN"), row);
        check("and points at the picture that answered it",
            row.split("|")[1] === psql(DB,
                `SELECT id::text FROM screenshots WHERE employee_id='E001' ORDER BY id DESC LIMIT 1`),
            row);

        synced = await sync(rajesh, "E001", "rajesh-laptop");
        check("the next sync asks for nothing — it is done",
            !synced.body.config?.capture_now, String(synced.body.config?.capture_now));

        res = await api("GET", `/admin/screenshot-requests/${requestId}`, { token: admin });
        check("and the page that was waiting can see it was taken",
            res.body.request?.status === "TAKEN", JSON.stringify(res.body).slice(0, 90));

        console.log("\nA request nobody answers");

        res = await api("POST", "/admin/employees/E001/screenshot", { token: admin });
        const stale = res.body.request_id;
        // The app was closed between the click and the poll.
        psql(DB, `UPDATE screenshot_requests
                     SET requested_at = (NOW() AT TIME ZONE 'UTC') - INTERVAL '6 minutes'
                   WHERE id = ${stale}`);

        synced = await sync(rajesh, "E001", "rajesh-laptop");
        check("a stale request is not handed to the client",
            !synced.body.config?.capture_now, String(synced.body.config?.capture_now));

        res = await api("GET", `/admin/screenshot-requests/${stale}`, { token: admin });
        check("and the page is told it expired rather than waiting for ever",
            res.body.request?.status === "EXPIRED", res.body.request?.status);

        // AND AN UPLOAD THAT NAMES NOTHING IS STILL A SCREENSHOT. The
        // scheduled captures carry no request id, and they must keep working
        // exactly as they did.
        const before = Number(psql(DB, `SELECT COUNT(*) FROM screenshots WHERE employee_id='E001'`));
        const plain = await upload(rajesh);
        check("a scheduled capture still uploads with no request at all",
            plain.status === 200
            && Number(psql(DB, `SELECT COUNT(*) FROM screenshots WHERE employee_id='E001'`)) === before + 1,
            `HTTP ${plain.status}`);
    } finally {
        if (server) server.close();
        try { require(path.join(root, "server", "config", "db")).end(); } catch (_) {}
        execFileSync("psql", ["-d", "postgres", "-c",
            `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`], { stdio: "pipe" });
        require("fs").rmSync(uploads, { recursive: true, force: true });
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.exit(1);
    }
    console.log("all on-demand screenshot checks passed");
    process.exit(0);
}

main();
