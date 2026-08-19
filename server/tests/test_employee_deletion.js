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

        // A FINALISED PAYROLL RUN, AND THEIR LINE IN IT. This is the record
        // that must survive the deletion — see below for what happened when
        // it did not.
        psql(DB, `INSERT INTO payroll_runs (month, status, working_days, generated_by)
                  VALUES ('2026-05-01','FINALIZED',22,'E001')`);
        const runId = psql(DB,
            `SELECT id FROM payroll_runs WHERE month='2026-05-01'`).trim();
        psql(DB, `INSERT INTO payroll_lines (run_id, employee_id, gross_monthly,
                      working_days, present_days, per_day, net_before_adjustments)
                  VALUES (${runId},'E001',50000,22,22,2272.73,50000)`);
        psql(DB, `INSERT INTO employee_salaries (employee_id, gross_monthly, effective_from)
                  VALUES ('E001', 50000, '2026-05-01')`);
        const runTotalBefore = psql(DB,
            `SELECT COALESCE(SUM(net_before_adjustments),0)
               FROM payroll_lines WHERE run_id=${runId}`).trim();

        // An alert that was sent about them — the send-log had no foreign key
        // and no explicit delete, so without one its rows outlive everything.
        psql(DB, `INSERT INTO alert_emails
                      (employee_id, alert_type, ist_day, severity, subject,
                       recipients, status)
                  VALUES ('E001','IDLE','2026-05-04','WARN','Idle too long',
                          'admin@example.com','SENT')`);

        console.log("Before");
        check("they have tracking data of every kind",
            count("attendance", "employee_id='E001'") === 1
            && count("screenshots", "employee_id='E001'") === 1
            && count("activity_logs", "employee_id='E001'") >= 1
            && count("idle_daily", "employee_id='E001'") === 1
            && count("employee_configs", "employee_id='E001'") === 1);
        check("and a message somebody else replied to",
            count("messages", "sender_id='E001'") === 1);
        check("and an alert was logged about them",
            count("alert_emails", "employee_id='E001'") === 1);

        // ── the deletion ────────────────────────────────────────────────
        const res = await api("DELETE", "/admin/employees/E001", { token: sa });
        console.log("\nThe deletion itself");
        check("succeeds", res.status === 200, JSON.stringify(res.body).slice(0, 120));
        check("and the account is gone",
            count("employees", "employee_id='E001'") === 0);

        console.log("\nA FINALISED PAYROLL RUN DOES NOT MOVE");
        // THE BUG THIS EXISTS FOR, measured in a scratch database:
        // payroll_lines.employee_id carried ON DELETE CASCADE, so deleting an
        // employee deleted their line out of a run that was already
        // FINALISED. The run's total went from 50,000.00 to 0 and the run
        // still said "FINALIZED". Nothing warned anybody and nothing in the
        // audit log said a number had moved.
        //
        // The product says everywhere else that a finalised month stops
        // moving. A payroll record that quietly changes is the one thing a
        // payroll record must never do.
        check("their payroll line is still there",
            count("payroll_lines", "employee_id='E001'") === 1,
            "a finalised run lost a line when somebody left");
        const runTotalAfter = psql(DB,
            `SELECT COALESCE(SUM(net_before_adjustments),0)
               FROM payroll_lines WHERE run_id=${runId}`).trim();
        check("and the run's total is exactly what it was",
            runTotalAfter === runTotalBefore,
            `${runTotalBefore} became ${runTotalAfter}`);
        check("salary history survives too — it is what the run was computed from",
            count("employee_salaries", "employee_id='E001'") === 1);
        check("the line still names them, as a former employee",
            psql(DB, `SELECT COALESCE(e.full_name, r.full_name, l.employee_id)
                        FROM payroll_lines l
                        LEFT JOIN employees e ON e.employee_id = l.employee_id
                        LEFT JOIN retired_employee_ids r ON r.employee_id = l.employee_id
                       WHERE l.employee_id = 'E001'`).trim().length > 0,
            "a line with an id and no name is not a record of anything");

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
        // The alert send-log was the one tracking table with no foreign key
        // and no explicit delete, so its rows outlived everything else.
        check("the alert send-log is removed too",
            count("alert_emails", "employee_id='E001'") === 0,
            "an orphan log about a former employee, kept for ever");
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
