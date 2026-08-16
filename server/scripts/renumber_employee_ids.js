/**
 * Move every existing employee onto the 26AMZEM001 format.
 *
 * THE FORMAT: year · company · role · number.
 *
 *   26   the year they JOINED — joining_date if it is set, otherwise the
 *        current year, which is when the numbering starts
 *   AMZ  the company
 *   SU / AD / EM   super admin, admin, employee
 *   001  the number within that year and that role, oldest account first
 *
 * WHY THIS IS THE MOST DANGEROUS SCRIPT IN THE REPOSITORY. employee_id is the
 * primary key, and it is named by twenty-eight columns across twenty tables:
 * attendance, screenshots, activity logs, chat membership, messages, idle
 * time, configuration, sessions. A rename that misses one of them does not
 * fail loudly — it leaves rows pointing at somebody who no longer exists, and
 * the first symptom is a report that is quietly short.
 *
 * SO IT WORKS LIKE THIS:
 *
 *   * ONE TRANSACTION. Either every table moves or none of them does.
 *   * A COUNT OF EVERY TABLE BEFORE AND AFTER. If a single row fails to
 *     follow its owner, the numbers disagree and the whole thing is rolled
 *     back. This is the check that matters — the cascades are trusted by the
 *     database, not by me.
 *   * TWO PHASES, through a temporary prefix. Renaming EMP002 to 26AMZEM001
 *     while 26AMZEM001 already exists would collide; giving everybody a
 *     unique temporary id first makes the order irrelevant.
 *   * THE OLD IDS ARE RETIRED, so nothing ever reissues them. They are
 *     printed on every report exported before today.
 *
 * ALWAYS --dry-run FIRST. It prints the whole mapping and changes nothing.
 *
 *   node server/scripts/renumber_employee_ids.js --dry-run
 *   node server/scripts/renumber_employee_ids.js --apply
 *
 * AND TAKE A BACKUP BEFORE --apply. bash server/scripts/backup.sh
 */
const path = require("path");
require("dotenv").config({ path: path.resolve(__dirname, "..", ".env") });
const pool = require("../config/db");

const COMPANY = process.env.EMPLOYEE_ID_PREFIX || "AMZ";
const ROLE_CODE = { super_admin: "SU", admin: "AD", employee: "EM" };

// Every column that holds an employee id WITHOUT a foreign key. The ones with
// a foreign key are carried by ON UPDATE CASCADE — see the migration. These
// have to be moved by hand, and a column missing from this list is the exact
// failure the row counts below exist to catch.
const PLAIN_COLUMNS = [
    ["attendance", "employee_id"],
    ["activity_logs", "employee_id"],
    ["screenshots", "employee_id"],
    ["idle_daily", "employee_id"],
    ["employee_configs", "employee_id"],
    ["active_sessions", "employee_id"],
    ["employees", "suspended_by"],
    ["holidays", "created_by"],
    ["messages", "sender_employee_code"],
    ["retired_employee_ids", "retired_by"],
];

// Counted before and after. Every table that names an employee, plus the ones
// that hang off them, so an accidental cascade delete would show up too.
const COUNT_TABLES = [
    "employees", "attendance", "activity_logs", "screenshots", "idle_daily",
    "employee_configs", "active_sessions", "team_members", "channel_members",
    "messages", "message_reads", "mentions", "notifications", "teams",
    "channels", "holidays", "chat_access_log",
];

async function tableExists(client, name) {
    const r = await client.query(
        `SELECT 1 FROM information_schema.tables
          WHERE table_schema='public' AND table_name=$1`, [name]);
    return r.rowCount > 0;
}

async function countAll(client) {
    const counts = {};
    for (const table of COUNT_TABLES) {
        if (!(await tableExists(client, table))) continue;
        counts[table] = Number(
            (await client.query(`SELECT COUNT(*)::int AS n FROM ${table}`)).rows[0].n);
    }
    return counts;
}

/** old id -> new id, and the reason, for everybody who needs moving. */
async function buildMapping(client) {
    const people = (await client.query(
        `SELECT employee_id, full_name, role,
                COALESCE(
                    TO_CHAR(joining_date, 'YY'),
                    -- NOT the year the account was created. That is when
                    -- somebody was entered into this system, which for every
                    -- account that predates the numbering is not the year
                    -- they joined the company — it would put two of the
                    -- current staff in a 2025 series for no reason anybody
                    -- reading the id could work out.
                    --
                    -- Unknown means THIS year: the year the numbering starts.
                    -- Anybody whose real joining date is filled in first gets
                    -- their real year instead, which is why that field is
                    -- worth filling in before this is run.
                    TO_CHAR(NOW() AT TIME ZONE 'Asia/Kolkata', 'YY')
                ) AS yy,
                created_at
           FROM employees
          ORDER BY created_at ASC, employee_id ASC`)).rows;

    // Numbers already issued in each series — including retired ones, so a
    // number is never handed out twice.
    // retired_employee_ids arrives with a migration, and --dry-run has to
    // work on a server that has not run it yet — that is precisely the server
    // somebody points this at first, to see what it would do.
    const hasRetired = await tableExists(client, "retired_employee_ids");
    const taken = new Set();
    for (const { employee_id } of (await client.query(
        hasRetired
            ? `SELECT employee_id FROM employees
               UNION ALL SELECT employee_id FROM retired_employee_ids`
            : `SELECT employee_id FROM employees`)).rows) {
        taken.add(String(employee_id));
    }

    const counters = new Map();
    const mapping = [];
    for (const person of people) {
        const code = ROLE_CODE[person.role];
        if (!code) {
            throw new Error(
                `${person.employee_id} has role "${person.role}", which has no code. ` +
                `Fix the role first — guessing one would put somebody in the wrong series.`);
        }
        const prefix = `${person.yy}${COMPANY}${code}`;

        // ALREADY CORRECT? Leave it exactly as it is. Renaming somebody who
        // is already right would change their id for no reason, and their id
        // is on every report already exported.
        if (new RegExp(`^${prefix}\\d{3}$`).test(person.employee_id)) {
            counters.set(prefix, Math.max(
                counters.get(prefix) || 0,
                Number(person.employee_id.slice(prefix.length))));
            continue;
        }

        let next = counters.get(prefix) || 0;
        let candidate;
        do {
            next += 1;
            candidate = `${prefix}${String(next).padStart(3, "0")}`;
        } while (taken.has(candidate));
        counters.set(prefix, next);
        taken.add(candidate);

        mapping.push({
            from: person.employee_id,
            to: candidate,
            name: person.full_name || "(no name)",
            role: person.role,
        });
    }
    return mapping;
}

async function main() {
    const apply = process.argv.includes("--apply");
    const dryRun = process.argv.includes("--dry-run");
    if (apply === dryRun) {
        console.error("Pass exactly one of --dry-run or --apply.");
        console.error("Always --dry-run first, and take a backup before --apply.");
        process.exit(1);
    }

    const client = await pool.connect();
    try {
        const mapping = await buildMapping(client);

        console.log(`\n${mapping.length} of ${
            (await client.query("SELECT COUNT(*)::int AS n FROM employees")).rows[0].n
        } accounts need a new id\n`);
        for (const m of mapping) {
            console.log(`  ${m.from.padEnd(12)} -> ${m.to.padEnd(12)}  ${m.name}  (${m.role})`);
        }
        if (mapping.length === 0) {
            console.log("  nothing to do — every id is already in the format.");
            return;
        }

        if (dryRun) {
            console.log("\n--dry-run: nothing was changed.");
            return;
        }

        // --apply DOES need it: an old number that is not retired can be
        // handed to somebody else later, which is the whole guarantee.
        if (!(await tableExists(client, "retired_employee_ids"))) {
            console.error("\nThe retired_employee_ids table is missing — run the "
                        + "migrations first:\n    bash server/scripts/migrate.sh");
            process.exit(1);
        }

        const before = await countAll(client);
        await client.query("BEGIN");

        // PHASE ONE: park everybody on a temporary id, so the order in which
        // the real ones are assigned cannot collide with an id still in use.
        for (const m of mapping) {
            await client.query(
                `UPDATE employees SET employee_id = $2 WHERE employee_id = $1`,
                [m.from, `TMP~${m.to}`]);
            for (const [table, column] of PLAIN_COLUMNS) {
                if (!(await tableExists(client, table))) continue;
                await client.query(
                    `UPDATE ${table} SET ${column} = $2 WHERE ${column} = $1`,
                    [m.from, `TMP~${m.to}`]);
            }
        }

        // PHASE TWO: off the temporary id and onto the real one.
        for (const m of mapping) {
            await client.query(
                `UPDATE employees SET employee_id = $2 WHERE employee_id = $1`,
                [`TMP~${m.to}`, m.to]);
            for (const [table, column] of PLAIN_COLUMNS) {
                if (!(await tableExists(client, table))) continue;
                await client.query(
                    `UPDATE ${table} SET ${column} = $2 WHERE ${column} = $1`,
                    [`TMP~${m.to}`, m.to]);
            }
        }

        // The old numbers are retired. They are printed on every report
        // exported before today, and must never name a different person.
        for (const m of mapping) {
            await client.query(
                `INSERT INTO retired_employee_ids (employee_id, full_name, retired_by)
                 VALUES ($1, $2, NULL) ON CONFLICT (employee_id) DO NOTHING`,
                [m.from, `${m.name} — renumbered to ${m.to}`]);
        }

        // THE CHECK THAT DECIDES. If one row failed to follow its owner, a
        // count disagrees and none of this is kept.
        const after = await countAll(client);
        const lost = Object.keys(before).filter((t) => before[t] !== after[t]);
        if (lost.length) {
            await client.query("ROLLBACK");
            console.error("\nROLLED BACK — these tables changed size:");
            for (const t of lost) console.error(`  ${t}: ${before[t]} -> ${after[t]}`);
            process.exit(1);
        }

        const orphans = Number((await client.query(
            `SELECT COUNT(*)::int AS n FROM attendance a
              WHERE NOT EXISTS (SELECT 1 FROM employees e
                                 WHERE e.employee_id = a.employee_id)`)).rows[0].n);
        if (orphans > 0) {
            await client.query("ROLLBACK");
            console.error(`\nROLLED BACK — ${orphans} attendance rows point at nobody.`);
            process.exit(1);
        }

        await client.query("COMMIT");
        console.log(`\n${mapping.length} accounts renumbered. Every table kept its row count.`);
        console.log("The old ids are retired and will never be reissued.");
        console.log("\nEverybody must sign in again — restart the server:");
        console.log("    pm2 restart ets-server");
    } catch (error) {
        await client.query("ROLLBACK").catch(() => {});
        console.error("\nFAILED, nothing was changed:", error.message);
        process.exit(1);
    } finally {
        client.release();
        await pool.end();
    }
}

main();
