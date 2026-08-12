/**
 * What the server does with input it did not expect.
 *
 * These are not exotic. Each one came out of driving the real API with the
 * kind of thing people and machines actually send, and each one answered with
 * a 500 — which tells the client "the server is broken" when the truth was
 * "that request cannot be served". A 500 is also what a retry loop treats as
 * worth trying again, so a single bad item could be sent for ever.
 *
 *  * A MESSAGE WITH A NUL BYTE. PostgreSQL text cannot hold one at all, so the
 *    insert failed and the handler turned that into "Internal server error".
 *    Somebody pasting from a terminal, a PDF or a badly encoded file could
 *    make sending fail with nothing to tell them what was wrong with it.
 *
 *  * AN OVERSIZED SCREENSHOT. Multer rejects a file past the limit BEFORE any
 *    handler runs, and that rejection travels as an error — which fell to the
 *    generic handler and came back 500. The client then retried the same
 *    25 MB file, for ever. The chat attachment route already answered this
 *    properly; the screenshot route did not.
 *
 *  * A SCREENSHOT ID THAT IS NOT A NUMBER. The id column is an integer, so a
 *    UUID from a stale link reached Postgres as text and came back as
 *    "invalid input syntax for type integer" — a 500 and a stack trace for
 *    something entirely ordinary.
 *
 * Run:  node server/tests/test_input_limits.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { migrate } = require("./_migrate");

const DB = `ets_limits_${process.pid}`;
const PORT = 8000 + ((process.pid + 733) % 1000);
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

async function upload(token, bytes, name = "shot.enc") {
    const form = new FormData();
    form.append("screenshot", new Blob([bytes]), name);
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

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    const uploads = fs.mkdtempSync(path.join(os.tmpdir(), "ets_limits_"));
    console.log(`Input limits (${DB})\n`);

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
            UPLOAD_DIR: uploads,
        });

        const { server, pool } = require(path.join(root, "server", "server.js"));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        const admin = await login("admin1", "admin-machine");
        const employee = await login("rajesh", "rajesh-laptop");

        const team = (await api("POST", "/admin/teams",
            { token: admin, body: { name: "Development" } })).body.team;
        await api("POST", `/admin/teams/${team.id}/members`,
            { token: admin, body: { employee_ids: ["E001"] } });
        const teams = (await api("GET", "/chat/me/teams", { token: employee })).body.teams || [];
        const channel = (teams[0] || {}).channels[0];

        const send = (body) => api("POST", `/chat/channels/${channel.id}/messages`, {
            token: employee,
            body: { body, client_msg_id: crypto.randomUUID() },
        });

        console.log("A message carrying characters a database cannot store");
        let res = await send("before\u0000after");
        check("a NUL byte does not produce a 500", res.status !== 500,
            `HTTP ${res.status} ${JSON.stringify(res.body).slice(0, 90)}`);
        check("the message is accepted, with the byte taken out", res.status === 201 || res.status === 200,
            `HTTP ${res.status}`);
        const stored = psql(DB, `SELECT body FROM messages ORDER BY seq DESC LIMIT 1`);
        check("and what is stored is the text somebody meant to send",
            stored === "beforeafter", JSON.stringify(stored));

        res = await send("bell\u0007and\u001bescape");
        check("other control characters are handled the same way", res.status !== 500,
            `HTTP ${res.status}`);

        console.log("\nThings people legitimately type must survive");
        res = await send("line one\nline two\tafter a tab");
        check("newlines and tabs are NOT stripped", res.status === 201 || res.status === 200,
            `HTTP ${res.status}`);
        const kept = psql(DB, `SELECT body FROM messages ORDER BY seq DESC LIMIT 1`);
        check("they are still there when it is read back",
            kept.includes("line two"), JSON.stringify(kept));
        res = await send("नमस्ते 🙏 中文 привет");
        check("and so are other alphabets and emoji", res.status === 201 || res.status === 200,
            `HTTP ${res.status}`);

        console.log("\nA screenshot larger than the limit");
        res = await upload(employee, Buffer.alloc(2048));
        check("an ordinary capture uploads", res.status === 200, `HTTP ${res.status}`);
        res = await upload(employee, Buffer.alloc(25 * 1024 * 1024));
        check("25 MB is refused with 413, not 500", res.status === 413,
            `HTTP ${res.status} — a 500 is what a retry loop tries again, for ever`);
        check("and the refusal says what the limit is",
            /10 MB/i.test(JSON.stringify(res.body)), JSON.stringify(res.body).slice(0, 120));
        check("the server is still serving afterwards",
            (await api("GET", "/admin/employees", { token: admin })).status === 200);

        console.log("\nA screenshot id that is not a number");
        res = await api("GET", "/screenshots/download/not-an-id", { token: admin });
        check("a stale or malformed link is a 404, not a 500", res.status === 404,
            `HTTP ${res.status}`);
        res = await api("GET", `/screenshots/download/${crypto.randomUUID()}`, { token: admin });
        check("a UUID where an integer belongs is a 404 too", res.status === 404,
            `HTTP ${res.status}`);
        res = await api("GET", "/screenshots/download/999999", { token: admin });
        check("and a number that simply does not exist is also 404", res.status === 404,
            `HTTP ${res.status}`);

        server.close();
        await pool.end();
    } finally {
        try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
        try { fs.rmSync(uploads, { recursive: true, force: true }); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.stdout.write("", () => process.exit(1));
    } else {
        console.log("all input limit checks passed");
        process.stdout.write("", () => process.exit(0));
    }
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
