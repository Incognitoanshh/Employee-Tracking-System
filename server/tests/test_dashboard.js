/**
 * The admin dashboard, the audit log page, and the two small endpoints behind
 * them — none of which had a test.
 *
 * WHY THESE. A coverage pass over all 132 routes found eleven that no test
 * mentions. These five are the ones an administrator looks at first: the
 * counts on the front page, the seven-day charts under them, the activity feed
 * beside them, the log page they open when something looks wrong, and who is
 * on leave today.
 *
 * WHAT IS CHECKED, and each is a way a dashboard misleads:
 *
 *   * THE DAY IS THE EMPLOYEE'S DAY. Every figure in this product is counted
 *     against the IST day — attendance, idle, activity minutes, the log page's
 *     own filter. The charts grouped by DATE() on a UTC column, so everything
 *     between midnight and half past five in the morning IST was drawn on
 *     yesterday's bar. A night shift's whole night lands on the wrong day.
 *   * THE FEED IS WHAT PEOPLE DID, not what the software said to itself.
 *     ConfigSyncManager and SchedulerService write dozens of lines an hour;
 *     unfiltered they bury the one line somebody is looking for.
 *   * COMPANY-WIDE MEANS ADMINS ONLY. Every one of these answers about
 *     everybody, so an employee must not be able to ask.
 *
 * Run:  node server/tests/test_dashboard.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_dash_${process.pid}`;
const PORT = 8000 + ((process.pid + 631) % 1000);
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

/**
 * "01:30" today in IST, as the naive UTC these columns actually hold.
 *
 * Before half past five in the morning IST, such a moment belongs to TODAY in
 * IST and to YESTERDAY in UTC — which is exactly what tells a chart grouped
 * on one apart from a chart grouped on the other, at any hour the suite runs.
 */
const istTodayAtUtc = (time) =>
    `((((NOW() AT TIME ZONE 'Asia/Kolkata')::date + TIME '${time}')`
    + ` AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'UTC')`;

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

const login = async (u, device = "d1") =>
    (await api("POST", "/auth/login",
        { body: { username: u, password: PASSWORD, device_id: device } })).body.token;

async function main() {
    const root = path.resolve(__dirname, "..", "..");
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
        });

        ({ server } = require(path.join(root, "server", "server.js")));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        const admin = await login("admin1", "admin-mac");
        const rajesh = await login("rajesh", "rajesh-laptop");

        console.log(`\nThe counts on the front page (${DB})\n`);

        let res = await api("GET", "/dashboard/stats", { token: rajesh });
        check("an employee cannot read the company's counts", res.status === 403,
            `HTTP ${res.status}`);

        res = await api("GET", "/dashboard/stats", { token: admin });
        check("an admin can", res.status === 200, `HTTP ${res.status}`);
        // THE ADMINS ARE NOT THE STAFF. "Total Employees" counting the people
        // who run the panel is a number nobody can reconcile with the list.
        check("and it counts the employees, not the administrators",
            res.body.data.employees === 2, JSON.stringify(res.body.data));
        check("with nothing to show for screenshots or logs yet",
            res.body.data.screenshots === 0 && res.body.data.activity_logs === 0,
            JSON.stringify(res.body.data));

        psql(DB, `INSERT INTO screenshots (employee_id, file_name, created_at)
            VALUES ('E001','a.enc', ${istTodayAtUtc("01:30")}),
                   ('E001','b.enc', ${istTodayAtUtc("10:00")})`);
        psql(DB, `INSERT INTO activity_logs (employee_id, activity, created_at) VALUES
            ('E001','USER ACTIVE', ${istTodayAtUtc("01:31")}),
            ('E001','ConfigSyncManager: sync OK', ${istTodayAtUtc("01:32")}),
            ('E001','SchedulerService: screenshot scheduled', ${istTodayAtUtc("01:33")}),
            ('E002','LOGIN', ${istTodayAtUtc("10:05")})`);

        res = await api("GET", "/dashboard/stats", { token: admin });
        check("the counts move when the data does",
            res.body.data.screenshots === 2 && res.body.data.activity_logs === 4,
            JSON.stringify(res.body.data));

        console.log("\nThe feed beside them");

        res = await api("GET", "/dashboard/recent-activity", { token: rajesh });
        check("an employee cannot read everybody's activity", res.status === 403,
            `HTTP ${res.status}`);

        res = await api("GET", "/dashboard/recent-activity", { token: admin });
        check("an admin can", res.status === 200, `HTTP ${res.status}`);
        const feed = res.body.data || res.body.activity || [];
        const text = JSON.stringify(feed);
        // WHAT PEOPLE DID, NOT WHAT THE SOFTWARE SAID TO ITSELF. The client
        // writes these housekeeping lines by the dozen; in the feed they bury
        // the one line somebody came to find.
        check("the software's own chatter is left out of it",
            !text.includes("ConfigSyncManager") && !text.includes("SchedulerService"),
            text.slice(0, 160));
        check("while what somebody actually did is in it",
            text.includes("USER ACTIVE") && text.includes("LOGIN"), text.slice(0, 160));

        console.log("\nThe seven-day charts");

        res = await api("GET", "/dashboard/charts", { token: rajesh });
        check("an employee cannot read the company's charts", res.status === 403,
            `HTTP ${res.status}`);

        res = await api("GET", "/dashboard/charts", { token: admin });
        check("an admin can", res.status === 200, `HTTP ${res.status}`);

        // ── THE DAY THE BARS ARE DRAWN AGAINST ────────────────────────────
        //
        // Both screenshots were taken today in IST — one at half past one in
        // the morning, one at ten. On the employee's own calendar that is one
        // day with two pictures in it. Grouped by the UTC date, the 01:30 one
        // falls on yesterday and the chart shows two days with one each.
        const istToday = psql(DB,
            "SELECT TO_CHAR(NOW() AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD')");
        const shots = res.body.data.screenshots_per_day || [];
        const onIstToday = shots.filter(
            (row) => String(row.date).slice(0, 10) === istToday);
        check("both of today's screenshots are counted on today",
            onIstToday.length === 1 && Number(onIstToday[0].count) === 2,
            JSON.stringify(shots));
        check("and nothing is drawn on a day nobody worked",
            shots.length === 1, JSON.stringify(shots));

        const activity = res.body.data.activity_per_day || [];
        check("the activity chart uses the same day as everything else",
            activity.every((row) => String(row.date).slice(0, 10) === istToday),
            JSON.stringify(activity));
        check("and leaves the software's own chatter out, like the feed does",
            activity.reduce((n, row) => n + Number(row.count), 0) === 2,
            JSON.stringify(activity));

        // Attendance, on the same rule.
        psql(DB, `INSERT INTO attendance (employee_id, login_time) VALUES
            ('E001', ${istTodayAtUtc("01:00")}), ('E002', ${istTodayAtUtc("09:30")})`);
        res = await api("GET", "/dashboard/charts", { token: admin });
        const shifts = res.body.data.attendance_per_day || [];
        const shiftsToday = shifts.filter(
            (row) => String(row.date).slice(0, 10) === istToday);
        check("a shift that started at one in the morning belongs to that day",
            shiftsToday.length === 1 && Number(shiftsToday[0].count) === 2,
            JSON.stringify(shifts));

        console.log("\nThe log page an admin opens when something looks wrong");

        res = await api("GET", "/admin/logs", { token: rajesh });
        check("an employee cannot read everybody's logs", res.status === 403,
            `HTTP ${res.status}`);

        res = await api("GET", "/admin/logs", { token: admin });
        check("an admin can", res.status === 200, `HTTP ${res.status}`);
        const all = res.body.data || res.body.logs || [];
        check("and sees more than one person's",
            new Set(all.map((r) => r.employee_id)).size === 2,
            JSON.stringify(all.map((r) => r.employee_id)));

        res = await api("GET", "/admin/logs?employee_id=E002", { token: admin });
        const only = res.body.data || res.body.logs || [];
        check("one person's logs can be asked for on their own",
            only.length > 0 && only.every((r) => r.employee_id === "E002"),
            JSON.stringify(only.map((r) => r.employee_id)));

        // THE DATE FILTER IS AN IST DATE, like the log page's own date picker.
        res = await api("GET", `/admin/logs?date=${istToday}`, { token: admin });
        const onDay = res.body.data || res.body.logs || [];
        check("and a day's logs include the small hours of that day",
            onDay.some((r) => String(r.activity).includes("USER ACTIVE")),
            JSON.stringify(onDay.map((r) => r.activity)).slice(0, 160));

        console.log("\nWho is on leave on a given day");

        psql(DB, `INSERT INTO leave_requests
                    (employee_id, leave_type, start_date, end_date, total_days,
                     reason, status)
                  VALUES ('E002','CASUAL', DATE '${istToday}', DATE '${istToday}', 1,
                          'a wedding', 'APPROVED')`);
        res = await api("GET", `/admin/leave/on/${istToday}`, { token: admin });
        check("the day's leave can be read", res.status === 200, `HTTP ${res.status}`);
        check("and it names who is off, not just how many",
            (res.body.on_leave || []).some((r) => r.employee_id === "E002"),
            JSON.stringify(res.body.on_leave));

        res = await api("GET", "/admin/leave/on/not-a-date", { token: admin });
        check("a date that is not a date is refused, not guessed at",
            res.status === 400, `HTTP ${res.status}`);

        console.log("\nVerbose logging, which an admin turns on for one machine");

        res = await api("POST", "/admin/toggle-verbose-logging",
            { token: admin, body: { employee_id: "E001", verbose_logging: true } });
        check("it can be switched on for one employee", res.status === 200,
            `HTTP ${res.status} ${JSON.stringify(res.body).slice(0, 80)}`);
        check("and it is stored against that employee alone",
            psql(DB, `SELECT verbose_logging FROM employee_configs
                       WHERE employee_id = 'E001'`) === "t"
            && psql(DB, `SELECT COUNT(*) FROM employee_configs
                          WHERE employee_id = 'E002'`) === "0",
            psql(DB, `SELECT employee_id || '=' || verbose_logging FROM employee_configs
                       WHERE employee_id IS NOT NULL`));

        res = await api("POST", "/admin/toggle-verbose-logging",
            { token: admin, body: { verbose_logging: true } });
        check("asking without saying whose machine is refused", res.status === 400,
            `HTTP ${res.status}`);

        res = await api("POST", "/admin/toggle-verbose-logging",
            { token: rajesh, body: { employee_id: "E002", verbose_logging: true } });
        check("and an employee cannot switch it on for somebody else",
            res.status === 403, `HTTP ${res.status}`);

        console.log("\nThe last two routes nothing had asked for");

        // ── THE ALERT EMAIL, SENT BY HAND ─────────────────────────────────
        //
        // The owner's button for "send the digest now". No SMTP is configured
        // here, and that is the case worth checking: it must come back saying
        // so rather than as a 500, because an administrator pressing it on a
        // server with no mail set up is the ordinary first attempt.
        const owner = await login("owner", "owner-mac");
        res = await api("POST", "/admin/alerts/email/run", { token: admin });
        check("an ordinary admin cannot send the company's alert digest",
            res.status === 403, `HTTP ${res.status}`);

        res = await api("POST", "/admin/alerts/email/run", { token: owner });
        check("the owner can, and with no mail configured it says so rather than failing",
            res.status === 200 && res.body.success === true,
            `HTTP ${res.status} ${JSON.stringify(res.body).slice(0, 120)}`);
        check("and it reports what it did rather than nothing at all",
            Object.keys(res.body).length > 1, JSON.stringify(res.body).slice(0, 120));

        // ── MARKING NOTIFICATIONS READ ────────────────────────────────────
        psql(DB, `INSERT INTO notifications (employee_id, type, is_read)
                  VALUES ('E001','MENTION', false), ('E002','MENTION', false)`);
        res = await api("POST", "/chat/notifications/read",
            { token: rajesh, body: {} });
        check("an employee can mark their notifications read", res.status === 200,
            `HTTP ${res.status}`);
        check("and it is theirs that are marked",
            psql(DB, `SELECT is_read FROM notifications WHERE employee_id='E001'`) === "t",
            psql(DB, `SELECT employee_id || '=' || is_read FROM notifications
                       ORDER BY employee_id`));
        // NOT EVERYBODY'S. One person clearing their own bell must not clear
        // the whole company's.
        check("while somebody else's are left alone",
            psql(DB, `SELECT is_read FROM notifications WHERE employee_id='E002'`) === "f",
            psql(DB, `SELECT employee_id || '=' || is_read FROM notifications
                       ORDER BY employee_id`));
    } finally {
        if (server) server.close();
        try { require(path.join(root, "server", "config", "db")).end(); } catch (_) {}
        execFileSync("psql", ["-d", "postgres", "-c",
            `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`], { stdio: "pipe" });
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.exit(1);
    }
    console.log("all dashboard checks passed");
    process.exit(0);
}

main();
