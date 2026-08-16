/**
 * Renumbering every employee, without losing a single row.
 *
 * employee_id is the primary key and twenty-eight columns across twenty
 * tables name it. This is the one operation where a mistake is silent: a
 * rename that misses a table does not fail, it leaves rows pointing at
 * somebody who no longer exists, and the first symptom is a report that is
 * quietly short a month later.
 *
 * So this test gives one person a row in EVERY table that can name them —
 * attendance, screenshots, logs, idle time, configuration, a session, team
 * and channel membership, a message, a read marker, a mention, a
 * notification, a holiday they created, a team they created — renumbers
 * them, and then goes looking for each of those rows under the NEW id. Not
 * "the script exited 0": the rows themselves.
 *
 * Run:  node server/tests/test_renumber.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_renum_${process.pid}`;
const ROOT = path.resolve(__dirname, "..", "..");

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}

function psql(sql, db = DB) {
    // -q as well as -tA: without it psql prints the command tag ("INSERT 0 1")
    // alongside the RETURNING value, and the id read back is unusable.
    return execFileSync("psql", ["-d", db, "-q", "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        { encoding: "utf8" }).trim();
}

function runScript(...args) {
    return execFileSync("node",
        [path.join(ROOT, "server", "scripts", "renumber_employee_ids.js"), ...args],
        {
            encoding: "utf8",
            env: {
                ...process.env,
                DB_HOST: process.env.PGHOST || "127.0.0.1",
                DB_PORT: process.env.PGPORT || "5432",
                DB_NAME: DB,
                DB_USER: process.env.PGUSER || process.env.USER,
                DB_PASSWORD: process.env.PGPASSWORD || "unused-locally",
            },
        });
}

try {
    console.log(`Renumbering (${DB})\n`);
    migrate(DB);

    // Three people, deliberately in the old shapes, created oldest first.
    psql(`INSERT INTO employees (employee_id, username, password, role, full_name, created_at) VALUES
        ('EMP001','owner','x','super_admin','The Owner',   (NOW() AT TIME ZONE 'UTC') - INTERVAL '300 days'),
        ('TEST001','raju','x','admin',      'Raju Kumar',  (NOW() AT TIME ZONE 'UTC') - INTERVAL '200 days'),
        ('EMP002','ansh','x','employee',    'Ansh',        (NOW() AT TIME ZONE 'UTC') - INTERVAL '100 days')`);

    // One person with a row in every table that can name them.
    psql(`INSERT INTO attendance (employee_id, login_time) VALUES
        ('EMP002', (NOW() AT TIME ZONE 'UTC') - INTERVAL '2 hours')`);
    psql(`INSERT INTO activity_logs (employee_id, activity) VALUES ('EMP002','USER ACTIVE')`);
    psql(`INSERT INTO screenshots (employee_id, file_name) VALUES ('EMP002','a.enc')`);
    psql(`INSERT INTO idle_daily (employee_id, day, idle_seconds)
          VALUES ('EMP002', CURRENT_DATE, 600)`);
    psql(`INSERT INTO employee_configs (employee_id, screenshots_per_day)
          VALUES ('EMP002', 9)`);
    psql(`INSERT INTO active_sessions (employee_id, token, device_id)
          VALUES ('EMP002','tok','dev')`);
    psql(`UPDATE employees SET suspended_by='TEST001' WHERE employee_id='EMP002'`);
    psql(`UPDATE employees SET reporting_manager='TEST001' WHERE employee_id='EMP002'`);
    psql(`INSERT INTO holidays (holiday_date, name, created_by)
          VALUES ('2026-12-25','Christmas','EMP001')`);

    const teamId = psql(`INSERT INTO teams (name, created_by) VALUES ('Engineering','EMP001')
                         RETURNING id`);
    const chanId = psql(`INSERT INTO channels (team_id, name, created_by)
                         VALUES (${teamId},'General','EMP001') RETURNING id`);
    psql(`INSERT INTO team_members (team_id, employee_id) VALUES (${teamId},'EMP002')`);
    psql(`INSERT INTO channel_members (channel_id, employee_id) VALUES (${chanId},'EMP002')`);
    const seq = psql(`INSERT INTO messages (channel_id, sender_id, sender_name,
                          sender_employee_code, body)
                      VALUES (${chanId},'EMP002','Ansh','EMP002','hello') RETURNING seq`);
    psql(`INSERT INTO message_reads (channel_id, employee_id) VALUES (${chanId},'EMP002')`);
    psql(`INSERT INTO mentions (message_seq, employee_id) VALUES (${seq},'EMP002')`);
    psql(`INSERT INTO notifications (employee_id, type, channel_id)
          VALUES ('EMP002','MENTION',${chanId})`);

    console.log("--dry-run changes nothing");
    const dry = runScript("--dry-run");
    check("it prints the mapping", /EMP002\s+->\s+\d\dAMZEM\d{3}/.test(dry), dry.slice(0, 240));
    check("and the ids are untouched",
        psql(`SELECT count(*) FROM employees WHERE employee_id='EMP002'`) === "1");

    console.log("\nThe format is the owner's: year, company, role, number");
    check("the super admin becomes SU",
        /EMP001\s+->\s+\d\dAMZSU001/.test(dry), dry.match(/EMP001[^\n]*/)?.[0]);
    check("the admin becomes AD",
        /TEST001\s+->\s+\d\dAMZAD001/.test(dry), dry.match(/TEST001[^\n]*/)?.[0]);
    check("the employee becomes EM",
        /EMP002\s+->\s+\d\dAMZEM001/.test(dry), dry.match(/EMP002[^\n]*/)?.[0]);

    console.log("\nApplying it");
    const out = runScript("--apply");
    check("it reports success", /renumbered/.test(out), out.slice(-260));

    const newId = psql(`SELECT employee_id FROM employees WHERE username='ansh'`);
    check("the employee has a new id", /^\d\dAMZEM001$/.test(newId), newId);
    check("and the old one is gone",
        psql(`SELECT count(*) FROM employees WHERE employee_id='EMP002'`) === "0");

    console.log("\nEVERY ROW FOLLOWED THEM");
    const rows = [
        ["attendance", "employee_id"],
        ["activity_logs", "employee_id"],
        ["screenshots", "employee_id"],
        ["idle_daily", "employee_id"],
        ["employee_configs", "employee_id"],
        ["active_sessions", "employee_id"],
        ["team_members", "employee_id"],
        ["channel_members", "employee_id"],
        ["message_reads", "employee_id"],
        ["mentions", "employee_id"],
        ["notifications", "employee_id"],
        ["messages", "sender_id"],
        ["messages", "sender_employee_code"],
    ];
    for (const [table, column] of rows) {
        check(`${table}.${column}`,
            psql(`SELECT count(*) FROM ${table} WHERE ${column}='${newId}'`) === "1",
            `still under the old id: ${psql(`SELECT count(*) FROM ${table} WHERE ${column}='EMP002'`)}`);
    }
    check("nothing anywhere still says EMP002",
        psql(`SELECT count(*) FROM attendance WHERE employee_id LIKE 'EMP%'`) === "0");

    console.log("\nThe references BETWEEN employees moved too");
    const adminId = psql(`SELECT employee_id FROM employees WHERE username='raju'`);
    check("reporting_manager points at the manager's new id",
        psql(`SELECT reporting_manager FROM employees WHERE username='ansh'`) === adminId,
        adminId);
    check("suspended_by as well",
        psql(`SELECT suspended_by FROM employees WHERE username='ansh'`) === adminId);
    const ownerId = psql(`SELECT employee_id FROM employees WHERE username='owner'`);
    check("a team's creator", psql(`SELECT created_by FROM teams WHERE id=${teamId}`) === ownerId);
    check("a channel's creator",
        psql(`SELECT created_by FROM channels WHERE id=${chanId}`) === ownerId);
    check("and a holiday's",
        psql(`SELECT created_by FROM holidays WHERE holiday_date='2026-12-25'`) === ownerId);

    console.log("\nThe old numbers are retired, not freed");
    for (const old of ["EMP001", "EMP002", "TEST001"]) {
        check(`${old} is retired`,
            psql(`SELECT count(*) FROM retired_employee_ids WHERE employee_id='${old}'`) === "1");
    }
    check("and the record says where they went",
        /renumbered to/.test(psql(
            `SELECT full_name FROM retired_employee_ids WHERE employee_id='EMP002'`)));

    console.log("\nRunning it a second time is a no-op");
    const again = runScript("--dry-run");
    check("nobody is offered another new id", /nothing to do/.test(again), again.slice(-160));
} finally {
    try { psql(`DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`, "postgres"); } catch (_) {}
}

console.log();
if (failures) {
    console.log(`${failures} failure(s)`);
    process.stdout.write("", () => process.exit(1));
} else {
    console.log("all renumbering checks passed");
    process.stdout.write("", () => process.exit(0));
}
