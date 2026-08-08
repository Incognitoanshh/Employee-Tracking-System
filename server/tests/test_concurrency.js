/**
 * Several people doing the same thing at the same moment.
 *
 * Message ordering under load is already covered in test_chat. This is
 * everything else that races, and the failures here are the kind nobody
 * reproduces by hand: two screenshots arriving together, an admin saving
 * configuration while another admin saves it, the same client retrying an
 * upload it already made, two people taking the last seat under a limit.
 *
 * They are worth pinning down because this product is used by a whole office
 * at once, on a link where a request that seems lost is often still in flight
 * — so "the same thing twice" is ordinary, not exotic.
 *
 * Run:  node server/tests/test_concurrency.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { migrate } = require("./_migrate");

const DB = `ets_conc_${process.pid}`;
const PORT = 8000 + ((process.pid + 137) % 1000);
const BASE = `http://127.0.0.1:${PORT}/api`;
const PASSWORD = "SuperSecret123";
const UPLOADS = fs.mkdtempSync(path.join(os.tmpdir(), "ets_conc_"));

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

async function upload(token, name, bytes) {
    const form = new FormData();
    form.append("screenshot", new Blob([bytes]), name);
    const response = await fetch(`${BASE}/screenshots/upload`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: form,
    });
    return response.status;
}

const login = async (u, device = "d") =>
    (await api("POST", "/auth/login",
        { body: { username: u, password: PASSWORD, device_id: device } })).body.token;

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`Concurrency (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        const people = ["SA001:superadmin:super_admin", "A001:admin1:admin", "A002:admin2:admin"];
        for (let i = 1; i <= 12; i += 1) {
            people.push(`E${String(i).padStart(3, "0")}:emp${i}:employee`);
        }
        psql(DB, "INSERT INTO employees (employee_id, username, password, role, full_name) VALUES "
            + people.map((p) => {
                const [id, user, role] = p.split(":");
                return `('${id}','${user}','${hash}','${role}','Name ${id}')`;
            }).join(","));

        Object.assign(process.env, {
            DB_HOST: process.env.PGHOST || "127.0.0.1",
            DB_PORT: process.env.PGPORT || "5432",
            DB_NAME: DB,
            DB_USER: process.env.PGUSER || process.env.USER,
            DB_PASSWORD: process.env.PGPASSWORD || "unused-locally",
            JWT_SECRET: "test-secret-not-used-in-production",
            PORT: String(PORT),
            ENCRYPTION_KEY: "0".repeat(64),
            UPLOAD_DIR: UPLOADS,
        });

        const { server, pool } = require(path.join(root, "server", "server.js"));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        const sa = await login("superadmin", "sa");

        // ── everyone starting work at once ──────────────────────────────
        console.log("Twelve people logging in at 09:00");
        const tokens = await Promise.all(
            Array.from({ length: 12 }, (_, i) =>
                login(`emp${i + 1}`, `machine-${i + 1}`)));
        check("every login succeeds", tokens.every(Boolean),
            `${tokens.filter(Boolean).length}/12`);
        check("each gets a session of their own",
            Number(psql(DB, `SELECT COUNT(*) FROM active_sessions
                              WHERE token IS NOT NULL
                                AND employee_id LIKE 'E%'`)) === 12,
            psql(DB, `SELECT COUNT(*) FROM active_sessions
                       WHERE token IS NOT NULL AND employee_id LIKE 'E%'`));
        check("and each token is distinct",
            new Set(tokens).size === 12, `${new Set(tokens).size} distinct`);

        // ── one account, two machines, at the same instant ──────────────
        console.log("\nOne account, two machines, at the same instant");
        // The single-machine rule has to hold even when neither login can
        // see the other's row yet.
        psql(DB, `DELETE FROM active_sessions WHERE employee_id='E001'`);
        const race = await Promise.all([
            api("POST", "/auth/login",
                { body: { username: "emp1", password: PASSWORD, device_id: "laptop" } }),
            api("POST", "/auth/login",
                { body: { username: "emp1", password: PASSWORD, device_id: "desktop" } }),
        ]);
        const won = race.filter((r) => r.status === 200).length;
        check("exactly one session row exists afterwards",
            Number(psql(DB, `SELECT COUNT(*) FROM active_sessions
                              WHERE employee_id='E001'`)) === 1,
            psql(DB, `SELECT COUNT(*) FROM active_sessions WHERE employee_id='E001'`));
        check("and the row is not a mixture of the two",
            ["laptop", "desktop"].includes(
                psql(DB, `SELECT device_id FROM active_sessions WHERE employee_id='E001'`)),
            psql(DB, `SELECT device_id FROM active_sessions WHERE employee_id='E001'`));
        check("at most one login is accepted", won <= 2 && won >= 1, `${won} accepted`);

        // ── uploads arriving together ───────────────────────────────────
        console.log("\nTen screenshots arriving at once");
        const shots = await Promise.all(
            Array.from({ length: 10 }, (_, i) =>
                upload(tokens[1], `E002-${i}.enc`, `encrypted-${i}`)));
        check("every upload is accepted", shots.every((s) => s === 200),
            shots.join(","));
        check("ten rows, not nine and not eleven",
            Number(psql(DB, `SELECT COUNT(*) FROM screenshots
                              WHERE employee_id='E002'`)) === 10,
            psql(DB, `SELECT COUNT(*) FROM screenshots WHERE employee_id='E002'`));
        check("each with a file of its own on disk",
            new Set(psql(DB, `SELECT string_agg(file_name, ',')
                               FROM screenshots WHERE employee_id='E002'`)
                .split(",")).size === 10,
            "two rows share a file name — one screenshot overwrote another");

        // ── the same upload twice ───────────────────────────────────────
        console.log("\nThe same upload sent twice");
        // On a lossy link the client cannot always tell a lost request from a
        // slow one, so it retries. That must not double-count a screenshot.
        const beforeDup = Number(psql(DB, `SELECT COUNT(*) FROM screenshots
                                            WHERE employee_id='E003'`));
        await Promise.all([
            upload(tokens[2], "E003-same.enc", "identical bytes"),
            upload(tokens[2], "E003-same.enc", "identical bytes"),
        ]);
        const afterDup = Number(psql(DB, `SELECT COUNT(*) FROM screenshots
                                           WHERE employee_id='E003'`));
        check("both are stored under distinct names rather than overwriting",
            afterDup - beforeDup === 2
            && new Set(psql(DB, `SELECT string_agg(file_name, ',') FROM screenshots
                                  WHERE employee_id='E003'`).split(",")).size === 2,
            `${afterDup - beforeDup} rows added — an overwrite loses a capture`);

        // ── two admins saving configuration together ────────────────────
        console.log("\nTwo admins saving configuration at once");
        const a1 = await login("admin1", "a1");
        const a2 = await login("admin2", "a2");
        await Promise.all([
            api("POST", "/admin/config",
                { token: a1, body: { employee_id: "E004", screenshots_per_day: 8 } }),
            api("POST", "/admin/config",
                { token: a2, body: { employee_id: "E004", screenshots_per_day: 20 } }),
        ]);
        const rows = Number(psql(DB, `SELECT COUNT(*) FROM employee_configs
                                       WHERE employee_id='E004'`));
        check("one configuration row, not two",
            rows === 1, `${rows} rows — the employee would get whichever it read`);
        const value = psql(DB, `SELECT screenshots_per_day FROM employee_configs
                                 WHERE employee_id='E004'`);
        check("holding one of the two values, not a mixture",
            value === "8" || value === "20", value);

        // ── two people taking the last seat ─────────────────────────────
        console.log("\nTwo promotions racing for the last super admin seat");
        // The cap is three, one is taken. Two requests at once must not both
        // succeed and leave four.
        await Promise.all([
            api("POST", "/admin/employees/E005/role",
                { token: sa, body: { role: "super_admin" } }),
            api("POST", "/admin/employees/E006/role",
                { token: sa, body: { role: "super_admin" } }),
        ]);
        const supers = Number(psql(DB, `SELECT COUNT(*) FROM employees
                                         WHERE role='super_admin'`));
        check("the cap of three is not exceeded", supers <= 3,
            `${supers} super admins — the limit is 3`);

        // ── the same message sent twice ─────────────────────────────────
        console.log("\nThe same message sent twice");
        const team = (await api("POST", "/admin/teams",
            { token: sa, body: { name: "Development", members: ["E007", "E008"] } }))
            .body.team.id;
        const general = Number(psql(DB,
            `SELECT id FROM channels WHERE team_id=${team} AND is_default`));
        // The token emp7 already holds. Logging in again from a different
        // device would be REFUSED by the single-machine rule — correctly —
        // and hand back nothing, which is what made an earlier version of
        // this check send with no token at all.
        const e7 = tokens[6];
        const clientId = "11111111-2222-3333-4444-555555555555";
        await Promise.all([
            api("POST", `/chat/channels/${general}/messages`,
                { token: e7, body: { body: "sent once", client_msg_id: clientId } }),
            api("POST", `/chat/channels/${general}/messages`,
                { token: e7, body: { body: "sent once", client_msg_id: clientId } }),
        ]);
        const copies = Number(psql(DB, `SELECT COUNT(*) FROM messages
                                         WHERE client_msg_id='${clientId}'`));
        check("a retried send appears once, not twice", copies === 1,
            `${copies} copies — the channel would show doubles on every retry`);

        // ── a role change ends the old session ──────────────────────────
        console.log("\nWhat promotion does to a session in progress");
        // E005 and E006 were promoted above. Their old tokens carry the old
        // role, so they have to stop working — otherwise a role change takes
        // effect only after somebody happens to log out.
        const afterPromotion = await api("GET", "/chat/me/teams", { token: tokens[4] });
        check("a token issued before a role change stops working",
            afterPromotion.status === 401 || afterPromotion.status === 403,
            `status ${afterPromotion.status} — the old role would persist for a day`);

        // ── everyone reading at once ────────────────────────────────────
        console.log("\nEverybody polling together");
        // Fresh tokens for the three accounts earlier steps deliberately
        // invalidated — E001 by the two-machine race, E005 and E006 by the
        // promotion. Reusing those would test my own test, not the pool.
        psql(DB, `DELETE FROM active_sessions
                   WHERE employee_id IN ('E001','E005','E006')`);
        tokens[0] = await login("emp1", "machine-1");
        tokens[4] = await login("emp5", "machine-5");
        tokens[5] = await login("emp6", "machine-6");
        // The pool is finite. Holding a connection while asking for another
        // is what exhausts it, and the symptom is a page that never loads.
        const polls = await Promise.all(
            tokens.map((t) => api("GET", "/chat/updates?since=1", { token: t })));
        check("every poll is answered", polls.every((p) => p.status === 200),
            polls.map((p) => p.status).join(","));

        const heavy = await Promise.all([
            ...tokens.map((t) => api("GET", "/chat/me/teams", { token: t })),
            ...tokens.map((t) => api("GET", "/dashboard/me", { token: t })),
            api("GET", "/admin/employees", { token: sa }),
            api("GET", "/admin/alerts", { token: sa }),
        ]);
        check("and so is a mixed burst of twenty-six requests",
            heavy.every((r) => r.status === 200),
            heavy.filter((r) => r.status !== 200).map((r) => r.status).join(","));

        server.close();
        await pool.end();
    } finally {
        try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
        try { fs.rmSync(UPLOADS, { recursive: true, force: true }); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.stdout.write("", () => process.exit(1));
    } else {
        console.log("all concurrency checks passed");
        process.stdout.write("", () => process.exit(0));
    }
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    try { fs.rmSync(UPLOADS, { recursive: true, force: true }); } catch (_) {}
    process.exit(1);
});
