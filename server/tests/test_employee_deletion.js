/**
 * What happens to everything when an employee is deleted.
 *
 * Deleting a person touches almost every table here, and the two ways to get
 * it wrong point in opposite directions. Leave too much and the company holds
 * screenshots and tracking data for people who left years ago — a retention
 * problem, and files nothing references so nothing can ever purge them. Delete
 * too much and a conversation loses half its messages, so the record everybody
 * else's messages sit in stops making sense.
 *
 * The design here draws that line deliberately: tracking data goes, the
 * conversation stays with the name it was sent under. These checks pin that
 * down, and look for what is quietly left behind.
 *
 * Run:  node server/tests/test_employee_deletion.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { migrate } = require("./_migrate");

const DB = `ets_del_${process.pid}`;
const PORT = 8000 + ((process.pid + 829) % 1000);
const BASE = `http://127.0.0.1:${PORT}/api`;
const PASSWORD = "SuperSecret123";
const UPLOADS = fs.mkdtempSync(path.join(os.tmpdir(), "ets_del_shots_"));

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}

function psql(db, sql) {
    return execFileSync("psql", ["-d", db, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        { encoding: "utf8" }).trim();
}

const count = (table, where) =>
    Number(psql(DB, `SELECT COUNT(*) FROM ${table} WHERE ${where}`));

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

const login = async (u, device = "d") =>
    (await api("POST", "/auth/login",
        { body: { username: u, password: PASSWORD, device_id: device } })).body.token;

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`Deleting an employee (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name) VALUES
            ('SA001','superadmin','${hash}','super_admin','Owner'),
            ('E001','rajesh','${hash}','employee','Rajesh Kumar'),
            ('E002','amit','${hash}','employee','Amit Sharma')`);

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

        const sa = await login("superadmin", "owner");
        const leaver = await login("rajesh", "leaver-laptop");

        // A working life, so there is something of each kind to lose.
        const team = (await api("POST", "/admin/teams",
            { token: sa, body: { name: "Development", members: ["E001", "E002"] } }))
            .body.team.id;
        const general = Number(psql(DB,
            `SELECT id FROM channels WHERE team_id=${team} AND is_default`));

        await api("POST", `/chat/channels/${general}/messages`,
            { token: leaver, body: { body: "kal ka report bhej dena" } });
        const theirMessage = Number(psql(DB,
            `SELECT MAX(seq) FROM messages WHERE sender_id='E001'`));
        const other = await login("amit", "amit-pc");
        await api("POST", `/chat/channels/${general}/messages`,
            { token: other, body: { body: "theek hai sir", reply_to: theirMessage } });

        psql(DB, `INSERT INTO attendance (employee_id, login_time)
                  VALUES ('E001', (NOW() AT TIME ZONE 'UTC'))`);
        psql(DB, `INSERT INTO activity_logs (employee_id, activity)
                  VALUES ('E001','USER ACTIVE')`);
        psql(DB, `INSERT INTO idle_daily (employee_id, day, idle_seconds)
                  VALUES ('E001', CURRENT_DATE, 600)`);
        psql(DB, `INSERT INTO employee_configs (employee_id, screenshots_per_day)
                  VALUES ('E001', 12)`);
        const shotFile = "E001-a-real-file.enc";
        fs.writeFileSync(path.join(UPLOADS, shotFile), "encrypted bytes");
        psql(DB, `INSERT INTO screenshots (employee_id, file_name)
                  VALUES ('E001','${shotFile}')`);

        console.log("Before");
        check("they have tracking data of every kind",
            count("attendance", "employee_id='E001'") === 1
            && count("screenshots", "employee_id='E001'") === 1
            && count("activity_logs", "employee_id='E001'") >= 1
            && count("idle_daily", "employee_id='E001'") === 1
            && count("employee_configs", "employee_id='E001'") === 1);
        check("and a message somebody else replied to",
            count("messages", "sender_id='E001'") === 1);

        // ── the deletion ────────────────────────────────────────────────
        const res = await api("DELETE", "/admin/employees/E001", { token: sa });
        console.log("\nThe deletion itself");
        check("succeeds", res.status === 200, JSON.stringify(res.body).slice(0, 120));
        check("and the account is gone",
            count("employees", "employee_id='E001'") === 0);

        console.log("\nTracking data goes with them");
        // The retention side: a company must not still hold pictures of the
        // screen of somebody who left.
        check("attendance is removed", count("attendance", "employee_id='E001'") === 0);
        check("screenshot rows are removed", count("screenshots", "employee_id='E001'") === 0);
        check("THE SCREENSHOT FILES TOO, not just the rows",
            !fs.existsSync(path.join(UPLOADS, shotFile)),
            "encrypted pictures of a former employee left on disk, referenced "
            + "by nothing, so nothing can ever purge or even find them");
        check("activity logs are removed", count("activity_logs", "employee_id='E001'") === 0);
        check("their configuration is removed",
            count("employee_configs", "employee_id='E001'") === 0);

        console.log("\nThe conversation does NOT");
        // The opposite failure: deleting somebody must not punch holes in a
        // record everybody else's messages sit in.
        check("their messages stay", count("messages", `seq=${theirMessage}`) === 1,
            "a channel would lose half its exchanges when somebody left");
        check("with the name they sent under, so it can still be attributed",
            psql(DB, `SELECT sender_name FROM messages WHERE seq=${theirMessage}`)
                === "Rajesh Kumar");
        check("but no longer pointing at an account",
            psql(DB, `SELECT sender_id IS NULL FROM messages WHERE seq=${theirMessage}`)
                === "t");

        let read = await api("GET", `/chat/channels/${general}/messages`, { token: other });
        check("the channel still reads correctly for everybody else",
            read.status === 200, `status ${read.status}`);
        const kept = (read.body.messages || []).find((m) => m.seq === theirMessage);
        check("their message is still in it", Boolean(kept), "the reply now answers nothing");
        check("marked as a former employee rather than as nobody",
            kept && kept.former === true, JSON.stringify(kept && kept.former));
        check("and the reply still quotes it",
            (read.body.messages || []).some(
                (m) => m.reply && m.reply.seq === theirMessage));

        console.log("\nMembership goes, because it means nothing without them");
        check("team membership is gone", count("team_members", "employee_id='E001'") === 0);
        check("channel membership is gone", count("channel_members", "employee_id='E001'") === 0);

        console.log("\nTheir session ends at once");
        // A still-valid JWT would otherwise work until it expired — up to a
        // day of access for somebody who has been removed.
        read = await api("GET", "/chat/me/teams", { token: leaver });
        check("their token stops working immediately",
            read.status === 401 || read.status === 403, `status ${read.status}`);

        console.log("\nWhat is left behind");
        // Not fatal, but real: rows nothing will ever look at again, in
        // tables with no foreign key to clean them up.
        const idleLeft = count("idle_daily", "employee_id='E001'");
        check("idle totals are not orphaned", idleLeft === 0,
            `${idleLeft} idle_daily row(s) for an employee who no longer exists`);
        const sessionLeft = count("active_sessions", "employee_id='E001'");
        check("no empty session row is left", sessionLeft === 0,
            `${sessionLeft} active_sessions row(s) left for a deleted account`);

        console.log("\nDeleting somebody who is not there");
        read = await api("DELETE", "/admin/employees/NOBODY", { token: sa });
        check("is a clean 404, not a crash", read.status === 404, `status ${read.status}`);

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
        console.log("all employee deletion checks passed");
        process.stdout.write("", () => process.exit(0));
    }
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    try { fs.rmSync(UPLOADS, { recursive: true, force: true }); } catch (_) {}
    process.exit(1);
});
