/**
 * Late / on-time / day-off, end to end.
 *
 * The unit-level rules are exercised directly, then the same rules are driven
 * through the real endpoint so the SQL that feeds them is covered too — the
 * IST conversion, the "first sign-in of the day" window function, and the
 * per-employee config fallback.
 *
 * Three things here are worth more than the rest:
 *
 *   * only the FIRST sign-in of a day can be late. Someone whose connection
 *     drops and who signs back in at 14:00 has not arrived five hours late,
 *     and flagging it that way makes the column worthless immediately.
 *
 *   * a day off outranks lateness. Marking somebody late on a holiday is the
 *     kind of thing that costs a person a day's pay.
 *
 *   * an overnight shift is measured from its own start. A 02:00 sign-in on a
 *     22:00-06:00 shift is four hours late, not twenty hours early.
 *
 * Run:  node server/tests/test_attendance_status.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_attstatus_${process.pid}`;
const PORT = 8000 + ((process.pid + 271) % 1000);
const BASE = `http://127.0.0.1:${PORT}/api`;

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
        ...(body ? { body: JSON.stringify(body) } : {}),
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return { status: response.status, body: payload };
}

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    const status = require(path.join(root, "server", "utils", "attendance_status"));

    console.log("Attendance status — the rules\n");

    const classify = (minutes, start, end, grace = 10, dayOff = false) =>
        status.classifyLogin({
            loginMinutes: minutes, shiftStart: start, shiftEnd: end,
            graceMinutes: grace, isDayOff: dayOff,
        });

    check("arriving exactly on time is on time",
        classify(540, "09:00", "18:00").status === "on_time");
    check("arriving inside the grace period is on time",
        classify(548, "09:00", "18:00").status === "on_time");
    check("arriving one minute past grace is late",
        classify(551, "09:00", "18:00").label === "Late 11m",
        classify(551, "09:00", "18:00").label);
    check("arriving early is on time, not negative-late",
        classify(500, "09:00", "18:00").status === "on_time");
    check("lateness over an hour reads in hours and minutes",
        classify(635, "09:00", "18:00").label === "Late 1h 35m",
        classify(635, "09:00", "18:00").label);
    check("a grace of 0 flags a single minute",
        classify(541, "09:00", "18:00", 0).label === "Late 1m",
        classify(541, "09:00", "18:00", 0).label);
    check("signing in after the shift ended is not called late",
        classify(1140, "09:00", "18:00").status === "outside_shift");
    check("a day off outranks lateness",
        classify(660, "09:00", "18:00", 10, true).status === "day_off");
    check("no configured shift gives no verdict",
        classify(540, null, null).status === "unknown");

    check("overnight: 22:05 on a 22:00 start is on time",
        classify(1325, "22:00", "06:00").status === "on_time");
    check("overnight: 22:45 is 45 minutes late",
        classify(1365, "22:00", "06:00").label === "Late 45m",
        classify(1365, "22:00", "06:00").label);
    check("overnight: 02:00 is four hours late, not twenty early",
        classify(120, "22:00", "06:00").label === "Late 4h",
        classify(120, "22:00", "06:00").label);

    check("overnight sign-in after midnight belongs to the previous day",
        status.shiftDayOffset(120, 1320, 360) === -1);
    check("overnight sign-in before midnight belongs to its own day",
        status.shiftDayOffset(1325, 1320, 360) === 0);
    check("a day shift never re-bases the day",
        status.shiftDayOffset(120, 540, 1080) === 0);

    // 2026-08-09 is a Sunday, 2026-08-08 a Saturday.
    check("Sunday counts as off when 7 is configured",
        status.isNonWorkingDay("2026-08-09", "7", new Set()));
    check("Saturday does not, when only 7 is configured",
        !status.isNonWorkingDay("2026-08-08", "7", new Set()));
    check("a holiday counts regardless of weekday",
        status.isNonWorkingDay("2026-08-05", "", new Set(["2026-08-05"])));
    check("junk weekly offs mean every day is a working day",
        !status.isNonWorkingDay("2026-08-09", "sunday", new Set()));

    console.log("\nThe same rules through the real endpoint\n");

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const seeded = await bcrypt.hash("SuperSecret123", 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role)
                  VALUES ('SA001', 'superadmin', '${seeded}', 'super_admin'),
                         ('E001', 'emp1', '${seeded}', 'employee')`);
        psql(DB, `INSERT INTO employee_configs
                    (employee_id, shift_start, shift_end, weekly_offs, late_grace_minutes)
                  VALUES ('E001', '09:00', '18:00', '7', 10)`);

        // Stored UTC; IST is +5:30. 03:30Z = 09:00 IST.
        const at = (day, utcTime) => `'2026-08-${day} ${utcTime}'`;
        psql(DB, `INSERT INTO attendance (employee_id, login_time, logout_time) VALUES
            ('E001', ${at("05", "03:30:00")}, ${at("05", "12:30:00")}),
            ('E001', ${at("05", "08:00:00")}, ${at("05", "09:00:00")}),
            ('E001', ${at("06", "04:15:00")}, ${at("06", "12:30:00")}),
            ('E001', ${at("07", "03:34:00")}, ${at("07", "12:30:00")}),
            ('E001', ${at("09", "05:00:00")}, ${at("09", "12:30:00")})`);
        //  05 Aug 09:00 IST -> on time      (and a 13:30 reconnect the same day)
        //  06 Aug 09:45 IST -> late 45m
        //  07 Aug 09:04 IST -> inside grace, on time
        //  09 Aug is a Sunday, and 7 is the weekly off -> day off

        psql(DB, `INSERT INTO holidays (holiday_date, name) VALUES ('2026-08-07', 'Test Holiday')`);
        // ...which should now outrank the on-time verdict for 07 Aug.

        process.env.DB_HOST = process.env.PGHOST || "127.0.0.1";
        process.env.DB_PORT = process.env.PGPORT || "5432";
        process.env.DB_NAME = DB;
        process.env.DB_USER = process.env.PGUSER || process.env.USER;
        process.env.DB_PASSWORD = process.env.PGPASSWORD || "unused-locally";
        process.env.JWT_SECRET = "test-secret-not-used-in-production";
        process.env.PORT = String(PORT);
        process.env.ENCRYPTION_KEY = "0".repeat(64);

        const { server, pool } = require(path.join(root, "server", "server.js"));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        let res = await api("POST", "/auth/login",
            { body: { username: "superadmin", password: "SuperSecret123" } });
        const token = res.body.token;

        res = await api("GET", "/attendance/all?employee_id=E001", { token });
        check("the endpoint responds", res.status === 200, `status ${res.status}`);

        const byTime = new Map();
        for (const row of res.body.data || []) {
            byTime.set(String(row.login_time).slice(0, 16), row);
        }
        const find = (prefix) =>
            [...byTime.entries()].find(([k]) => k.startsWith(prefix))?.[1];

        const onTime = find("2026-08-05 03:30");
        check("09:00 IST against a 09:00 shift is On time",
            onTime?.status === "on_time", JSON.stringify(onTime?.status_label));

        const reconnect = find("2026-08-05 08:00");
        check("a second sign-in the same day is not judged at all",
            reconnect?.status === "reconnect", JSON.stringify(reconnect?.status_label));
        // AND IT SAYS WHICH KIND OF "not judged" IT IS. A dash here read
        // identically to "no shift configured", so the same person on the
        // same morning saw "On time" on one row and "—" on the next, a
        // minute apart, and reported it as a bug. Two meanings, one symbol.
        check("and it says so in words rather than a dash",
            reconnect?.status_label === "Signed in again",
            JSON.stringify(reconnect?.status_label));
        check("no row is left showing a bare dash",
            !(res.body.data || []).some((r) => (r.status_label || "").trim() === "—"),
            JSON.stringify((res.body.data || []).map((r) => r.status_label)));

        const late = find("2026-08-06 04:15");
        check("09:45 IST is Late 45m",
            late?.status === "late" && late?.late_minutes === 45,
            JSON.stringify({ s: late?.status, m: late?.late_minutes }));

        const holiday = find("2026-08-07 03:34");
        check("a holiday outranks the on-time verdict",
            holiday?.status === "day_off", JSON.stringify(holiday?.status_label));

        const sunday = find("2026-08-09 05:00");
        check("the weekly off is applied",
            sunday?.status === "day_off", JSON.stringify(sunday?.status_label));

        // Grace is per employee and takes effect on the next read, with no
        // stored value to migrate.
        await api("POST", "/admin/config", { token, body: {
            employee_id: "E001", screenshot_min_minutes: 3, screenshot_max_minutes: 10,
            screenshots_per_day: 10, upload_interval_minutes: 60,
            idle_threshold_seconds: 60, shift_start: "09:00", shift_end: "18:00",
            weekly_offs: [7], late_grace_minutes: 60 } });
        res = await api("GET", "/attendance/all?employee_id=E001", { token });
        const relaxed = (res.body.data || []).find(
            (r) => String(r.login_time).startsWith("2026-08-06 04:15"));
        check("raising the grace period reclassifies history immediately",
            relaxed?.status === "on_time", JSON.stringify(relaxed?.status_label));

        res = await api("POST", "/admin/config", { token, body: {
            employee_id: "E001", screenshot_min_minutes: 3, screenshot_max_minutes: 10,
            screenshots_per_day: 10, upload_interval_minutes: 60,
            idle_threshold_seconds: 60, late_grace_minutes: 200 } });
        check("an absurd grace period is refused", res.status === 400, `status ${res.status}`);

        server.close();
        await pool.end();

    } finally {
        try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.exit(1);
    }
    console.log("all attendance status checks passed");
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
