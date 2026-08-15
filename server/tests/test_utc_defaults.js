/**
 * A timestamp default writes UTC, wherever the server happens to be.
 *
 * Every timestamp column in this schema except active_sessions is TIMESTAMP
 * WITHOUT TIME ZONE holding UTC, and every reader — utils/ist_sql.js,
 * presence.js, the reports — assumes exactly that.
 *
 * The columns defaulted to plain `now()`, a timestamptz. Assigning one to a
 * naive column converts it USING THE SESSION'S TIMEZONE, so what was stored
 * was the machine's wall clock. The column says UTC, the reader assumes UTC,
 * and the writer wrote local time.
 *
 * IT COULD NOT BE SEEN ON THE PRODUCTION SERVER, which runs in UTC — there
 * the two are the same instant and every row is correct. It appeared the
 * first time the same code ran on a laptop at +05:30: a screenshot taken at
 * 21:27 IST was stored as 21:27, read as UTC, and counted into the NEXT IST
 * day. An existing check in test_profile.js passed all morning and began
 * failing after 18:30 UTC, on code nobody had touched.
 *
 * So this file does what no other test could: it asks the question from a
 * session that is NOT in UTC. On a UTC machine the old defaults pass every
 * test there is, which is precisely how this survived.
 *
 * Run:  node server/tests/test_utc_defaults.js
 */
const { execFileSync } = require("child_process");
const { migrate } = require("./_migrate");

const DB = `ets_utcdef_${process.pid}`;

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}

function psql(db, sql, env) {
    return execFileSync("psql", ["-d", db, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        { encoding: "utf8", env: { ...process.env, ...(env || {}) } }).trim();
}

/**
 * Run SQL in a session pinned to a timezone that is NOT UTC.
 *
 * Through PGTZ rather than a leading `SET TIME ZONE`: psql prints one result
 * per statement, so the SET came back as part of the answer and every
 * comparison read "SET" instead of a number.
 */
function inKolkata(sql) {
    return psql(DB, sql, { PGTZ: "Asia/Kolkata" });
}

try {
    console.log(`UTC defaults (${DB})\n`);
    migrate(DB);

    psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name)
              VALUES ('U001','utcuser','x','employee','UTC User')`);

    // THE COLUMNS THAT MATTER MOST: the two the IST day is counted from.
    // A screenshot or a log line in the wrong day is a report that is wrong
    // for somebody's pay.
    console.log("A row inserted from a +05:30 session");
    inKolkata(`INSERT INTO screenshots (employee_id, file_name) VALUES ('U001','x.enc')`);
    inKolkata(`INSERT INTO activity_logs (employee_id, activity) VALUES ('U001','USER ACTIVE')`);

    // Compared against UTC "now" from a session that is also not UTC, so the
    // comparison itself cannot be what makes this pass.
    const shotDrift = Number(inKolkata(
        `SELECT ABS(EXTRACT(EPOCH FROM
             (NOW() AT TIME ZONE 'UTC') - created_at))::int
           FROM screenshots WHERE employee_id='U001'`));
    check("a screenshot's created_at is UTC, not the session's wall clock",
        shotDrift < 120,
        `${shotDrift} seconds out — 19800 is exactly the +05:30 offset`);

    const logDrift = Number(inKolkata(
        `SELECT ABS(EXTRACT(EPOCH FROM
             (NOW() AT TIME ZONE 'UTC') - created_at))::int
           FROM activity_logs WHERE employee_id='U001'`));
    check("and so is an activity log's", logDrift < 120,
        `${logDrift} seconds out`);

    // The whole point of getting it right: which IST day the row belongs to.
    // Between 18:30 and 24:00 UTC the two answers differ, and that is when
    // the reports would have been wrong.
    const sameDay = inKolkata(
        `SELECT DATE(created_at + INTERVAL '5 hours 30 minutes')
                = DATE((NOW() AT TIME ZONE 'UTC') + INTERVAL '5 hours 30 minutes')
           FROM screenshots WHERE employee_id='U001'`);
    check("so it is counted into today's IST day, at any hour", sameDay === "t",
        sameDay);

    console.log("\nNo naive column is left defaulting to local time");
    // Swept rather than listed. A column added later with `DEFAULT now()`
    // would reintroduce this silently, and nobody would look — which is how
    // it got here in the first place.
    const stragglers = psql(DB,
        `SELECT string_agg(table_name || '.' || column_name, ', ' ORDER BY table_name)
           FROM information_schema.columns
          WHERE table_schema = 'public'
            AND data_type = 'timestamp without time zone'
            AND column_default LIKE '%now()%'
            AND column_default NOT LIKE '%UTC%'`);
    check("every timestamp default names UTC", stragglers === "",
        stragglers || "(none)");
} finally {
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
}

console.log();
if (failures) {
    console.log(`${failures} failure(s)`);
    process.exit(1);
}
console.log("all UTC default checks passed");
