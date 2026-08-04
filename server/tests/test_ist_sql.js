/**
 * Verifies the IST SQL helpers against a real PostgreSQL instance.
 *
 * The bug this guards against is not a typo — it is that `AT TIME ZONE`
 * means two opposite things depending on whether its input is naive or
 * tz-aware. The old inline SQL used the column form for NOW() as well,
 * which silently produced the UTC date instead of the IST one and made the
 * dashboard's "today" figures read zero for five and a half hours a day.
 *
 * Run:  node server/tests/test_ist_sql.js
 *       (needs a reachable PostgreSQL; set PGHOST/PGUSER/PGDATABASE or use
 *        the server's own .env)
 */
const { execFileSync } = require("child_process");
const { istDate, istToday, isTodayIST } = require("../utils/ist_sql");

const DB = process.env.PGDATABASE || "postgres";

function q(sql) {
    return execFileSync("psql", ["-d", DB, "-tAc", sql], {
        encoding: "utf8",
        env: { ...process.env, PGOPTIONS: "-c timezone=UTC" },  // as production runs
    }).trim();
}

let failures = 0;
function check(label, actual, expected) {
    const ok = String(actual) === String(expected);
    if (!ok) failures++;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}  ->  ${actual}${ok ? "" : `  (expected ${expected})`}`);
}

console.log("IST SQL helpers, against PostgreSQL with timezone=UTC\n");

// The authoritative answer, computed independently of the helpers.
const istNow = q("SELECT to_char(NOW() AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD')");
console.log(`  reference IST date: ${istNow}\n`);

check("istToday() gives today's IST date", q(`SELECT ${istToday()}`), istNow);

// A naive-UTC column holding "now" must land on the same IST day.
check(
    "istDate(column) on a UTC 'now' value",
    q(`SELECT ${istDate("t")} FROM (SELECT (NOW() AT TIME ZONE 'UTC') AS t) s`),
    istNow
);

// The comparison the controllers actually use: today matches, ±1 day does not.
check(
    "isTodayIST matches today",
    q(`SELECT COUNT(*) FROM (SELECT (NOW() AT TIME ZONE 'UTC') AS created_at) s WHERE ${isTodayIST("created_at")}`),
    "1"
);
check(
    "isTodayIST rejects yesterday",
    q(`SELECT COUNT(*) FROM (SELECT (NOW() AT TIME ZONE 'UTC') - INTERVAL '1 day' AS created_at) s WHERE ${isTodayIST("created_at")}`),
    "0"
);
check(
    "isTodayIST rejects tomorrow",
    q(`SELECT COUNT(*) FROM (SELECT (NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day' AS created_at) s WHERE ${isTodayIST("created_at")}`),
    "0"
);

// The specific regression: an instant that is "today" in IST but "yesterday"
// in UTC. Between 18:30 and 24:00 UTC every day, these disagree — that window
// is where the old SQL returned nothing.
const utcDate = q("SELECT to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD')");
console.log(`\n  UTC date is ${utcDate}, IST date is ${istNow}` +
            (utcDate === istNow ? "  (same today — the divergent window is 18:30-24:00 UTC)"
                                : "  (DIVERGENT — this is the window the old SQL broke in)"));
check(
    "istToday() follows IST, not UTC",
    q(`SELECT ${istToday()} = DATE(NOW() AT TIME ZONE 'Asia/Kolkata')`),
    "t"
);

// The interpolation guard.
console.log("\n  injection guard:");
for (const bad of ["created_at; DROP TABLE employees", "1=1", "created_at)--", "$1"]) {
    try {
        istDate(bad);
        console.log(`  FAIL  accepted ${JSON.stringify(bad)}`);
        failures++;
    } catch {
        console.log(`  PASS  refused ${JSON.stringify(bad)}`);
    }
}

console.log();
if (failures) {
    console.log(`${failures} failure(s)`);
    process.exit(1);
}
console.log("all IST SQL checks passed");
