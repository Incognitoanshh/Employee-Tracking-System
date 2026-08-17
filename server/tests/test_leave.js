/**
 * Leave: who may ask, who may decide, and what it does to attendance.
 *
 * THE RULE THIS FILE EXISTS FOR is the last section: approved leave must show
 * as LEAVE and not as ABSENT, in the attendance list and in the report. That
 * number is what a timesheet is built from and what somebody is paid on, and
 * before this feature a day off with permission and a day somebody simply did
 * not turn up were the same figure.
 *
 * The rest is the boundary. An employee may apply, read their own, and cancel
 * one that is still pending. They may not cancel an approved one — the roster
 * has been planned around it — and they may not touch anybody else's. Any
 * admin may approve, reject, or revoke an approval that was wrong.
 *
 * Run:  node server/tests/test_leave.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { migrate } = require("./_migrate");

const DB = `ets_leave_${process.pid}`;
const PORT = 8000 + ((process.pid + 517) % 1000);
const BASE = `http://127.0.0.1:${PORT}/api`;
const PASSWORD = "SuperSecret123";

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}

function psql(sql, db = DB) {
    return execFileSync("psql", ["-d", db, "-q", "-v", "ON_ERROR_STOP=1", "-tAc", sql],
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

const login = async (username, device) =>
    (await api("POST", "/auth/login",
        { body: { username, password: PASSWORD, device_id: device } })).body.token;

/** A date n days from today, as YYYY-MM-DD in IST. */
const dayFromNow = (n) => psql(
    `SELECT TO_CHAR((NOW() AT TIME ZONE 'Asia/Kolkata')::date + ${n}, 'YYYY-MM-DD')`);

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    const uploads = fs.mkdtempSync(path.join(os.tmpdir(), "ets_leave_"));
    console.log(`Leave (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        // Created a month ago. The report deliberately does not count days
        // before somebody joined — "joined on the 20th was not absent on the
        // 5th" — so an account created this instant has no past to report on.
        psql(`INSERT INTO employees (employee_id, username, password, role,
                                     full_name, email, created_at)
              VALUES ('A001','admin1','${hash}','admin','Priya Nair','priya@x.test',
                      (NOW() AT TIME ZONE 'UTC') - INTERVAL '30 days'),
                     ('E001','rajesh','${hash}','employee','Rajesh Kumar','rajesh@x.test',
                      (NOW() AT TIME ZONE 'UTC') - INTERVAL '30 days'),
                     ('E002','sneha','${hash}','employee','Sneha Iyer',NULL,
                      (NOW() AT TIME ZONE 'UTC') - INTERVAL '30 days')`);
        // No weekly offs, so the arithmetic in these tests is the plain
        // number of days and nothing is quietly skipped.
        // The global row already exists — one is created by the schema, and
        // a unique index enforces that there is only ever one.
        psql(`UPDATE employee_configs SET weekly_offs = '', shift_start = '09:00',
                     shift_end = '18:00' WHERE employee_id IS NULL`);

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
        const rajesh = await login("rajesh", "rajesh-laptop");
        const sneha = await login("sneha", "sneha-laptop");

        const start = dayFromNow(3);
        const end = dayFromNow(5);

        console.log("Applying");
        let res = await api("POST", "/leave", { token: rajesh, body: {
            leave_type: "CASUAL", reason: "Family wedding",
            start_date: start, end_date: end } });
        check("a request is accepted", res.status === 201,
            `HTTP ${res.status} ${JSON.stringify(res.body).slice(0, 120)}`);
        check("three days is three days", Number(res.body.leave.total_days) === 3,
            String(res.body.leave?.total_days));
        check("and it starts as pending", res.body.leave.status === "PENDING");
        const first = res.body.leave.id;

        check("it is written to the audit log, which is where pay questions go",
            psql(`SELECT COUNT(*) FROM activity_logs
                   WHERE employee_id='E001' AND activity LIKE 'LEAVE APPLIED%'`) === "1");

        console.log("\nWhat is refused, and why");
        for (const [label, body, expect] of [
            ["a type nobody has heard of",
             { leave_type: "HOLIDAY", reason: "x", start_date: start, end_date: end }, 400],
            ["no reason at all",
             { leave_type: "SICK", reason: "  ", start_date: start, end_date: end }, 400],
            ["dates the wrong way round",
             { leave_type: "SICK", reason: "flu", start_date: end, end_date: start }, 400],
            ["a date that is not a date",
             { leave_type: "SICK", reason: "flu", start_date: "5th", end_date: end }, 400],
            ["half a day across three days",
             { leave_type: "CASUAL", reason: "x", start_date: start, end_date: end,
               half_day: true }, 400],
        ]) {
            res = await api("POST", "/leave", { token: sneha, body });
            check(label + " is refused", res.status === expect, `HTTP ${res.status}`);
        }

        console.log("\nTwo requests for the same day");
        res = await api("POST", "/leave", { token: rajesh, body: {
            leave_type: "SICK", reason: "overlaps on purpose",
            start_date: dayFromNow(4), end_date: dayFromNow(6) } });
        check("an overlapping request is refused", res.status === 409, `HTTP ${res.status}`);
        check("and says what it clashes with",
            /pending|approved/i.test(res.body.message || ""), res.body.message);

        console.log("\nA half day");
        res = await api("POST", "/leave", { token: sneha, body: {
            leave_type: "CASUAL", reason: "Dentist",
            start_date: dayFromNow(2), end_date: dayFromNow(2), half_day: true } });
        check("is accepted", res.status === 201, `HTTP ${res.status}`);
        check("and costs half a day", Number(res.body.leave.total_days) === 0.5,
            String(res.body.leave?.total_days));

        console.log("\nWho can see what");
        res = await api("GET", "/leave/mine", { token: rajesh });
        check("an employee sees their own", res.status === 200
            && res.body.leave.length === 1, JSON.stringify(res.body).slice(0, 120));
        check("and only their own",
            res.body.leave.every((l) => !l.employee_id || l.employee_id === "E001"));
        res = await api("GET", "/admin/leave", { token: rajesh });
        check("an employee cannot open the admin list", res.status === 403,
            `HTTP ${res.status}`);
        res = await api("GET", "/admin/leave", { token: admin });
        check("an admin sees everybody's", res.status === 200 && res.body.total === 2,
            `${res.body.total}`);
        check("pending ones come first — it is a queue",
            res.body.data[0].status === "PENDING");

        console.log("\nCancelling");
        res = await api("POST", `/leave/${first}/cancel`, { token: sneha });
        check("somebody else's request cannot be cancelled", res.status === 404,
            `HTTP ${res.status} — and 404, so it does not confirm the request exists`);
        res = await api("POST", `/leave/${first}/cancel`, { token: rajesh });
        check("your own pending one can", res.status === 200, `HTTP ${res.status}`);
        check("and it is recorded as cancelled",
            psql(`SELECT status FROM leave_requests WHERE id=${first}`) === "CANCELLED");
        check("the audit log has it",
            psql(`SELECT COUNT(*) FROM activity_logs
                   WHERE activity LIKE 'LEAVE CANCELLED%'`) === "1");

        console.log("\nApproving and rejecting");
        res = await api("POST", "/leave", { token: rajesh, body: {
            leave_type: "SICK", reason: "Fever",
            start_date: dayFromNow(10), end_date: dayFromNow(11) } });
        const second = res.body.leave.id;

        res = await api("POST", `/admin/leave/${second}/approve`, { token: rajesh });
        check("an employee cannot approve their own", res.status === 403,
            `HTTP ${res.status}`);
        res = await api("POST", `/admin/leave/${second}/reject`, { token: admin, body: {} });
        check("a rejection without a reason is refused", res.status === 400,
            `HTTP ${res.status} — the employee reads this, and "Rejected" alone `
            + `is the conversation this feature exists to save`);
        res = await api("POST", `/admin/leave/${second}/approve`,
            { token: admin, body: { remarks: "Get well" } });
        check("an admin can approve", res.status === 200, `HTTP ${res.status}`);
        check("with who decided and when",
            psql(`SELECT approved_by FROM leave_requests WHERE id=${second}`) === "A001"
            && psql(`SELECT approved_at IS NOT NULL FROM leave_requests
                      WHERE id=${second}`) === "t");
        res = await api("POST", `/admin/leave/${second}/approve`, { token: admin });
        check("approving twice is refused rather than silently repeated",
            res.status === 409, `HTTP ${res.status}`);

        console.log("\nNOBODY DECIDES THEIR OWN LEAVE");
        // An administrator is an employee too and takes leave like anybody
        // else. What must not happen is that the same person asks and grants:
        // an approval is somebody ELSE agreeing, and this was open — an admin
        // could apply and approve in two clicks, with their own id in
        // approved_by.
        res = await api("POST", "/leave", { token: admin, body: {
            leave_type: "CASUAL", reason: "My own holiday",
            start_date: dayFromNow(30), end_date: dayFromNow(31) } });
        check("an admin can apply for leave like anybody else",
            res.status === 201, `HTTP ${res.status}`);
        const own = res.body.leave.id;

        res = await api("POST", `/admin/leave/${own}/approve`, { token: admin });
        check("but cannot approve it themselves", res.status === 403,
            `HTTP ${res.status}`);
        check("and is told to ask somebody else",
            /another administrator/i.test(res.body.message || ""), res.body.message);
        check("it is still waiting",
            psql(`SELECT status FROM leave_requests WHERE id=${own}`) === "PENDING");
        res = await api("POST", `/admin/leave/${own}/reject`,
            { token: admin, body: { remarks: "changed my mind" } });
        check("nor reject it", res.status === 403, `HTTP ${res.status}`);

        console.log("\nAN ADMIN'S LEAVE IS THE SUPER ADMIN'S TO DECIDE");
        // The same hierarchy the rest of the product enforces: an admin
        // manages employees, and only the super admin manages admins.
        psql(`INSERT INTO employees (employee_id, username, password, role,
                                     full_name, created_at)
              VALUES ('A002','admin2','${hash}','admin','Second Admin',
                      (NOW() AT TIME ZONE 'UTC') - INTERVAL '30 days'),
                     ('SA01','owner','${hash}','super_admin','The Owner',
                      (NOW() AT TIME ZONE 'UTC') - INTERVAL '30 days')`);
        const admin2 = await login("admin2", "admin2-machine");
        const owner = await login("owner", "owner-machine");

        res = await api("POST", "/leave", { token: admin2, body: {
            leave_type: "CASUAL", reason: "A break",
            start_date: dayFromNow(40), end_date: dayFromNow(41) } });
        const adminLeave = res.body.leave.id;
        check("an admin's request is created", res.status === 201, `HTTP ${res.status}`);

        res = await api("POST", `/admin/leave/${adminLeave}/approve`, { token: admin });
        check("ANOTHER ADMIN CANNOT DECIDE IT", res.status === 403, `HTTP ${res.status}`);
        check("and is told why",
            /admin/i.test(res.body.message || ""), res.body.message);
        res = await api("POST", `/admin/leave/${adminLeave}/approve`, { token: owner });
        check("the super admin can", res.status === 200, `HTTP ${res.status}`);

        console.log("\nAn employee cannot take back an approved one");
        res = await api("POST", `/leave/${second}/cancel`, { token: rajesh });
        check("cancelling approved leave is refused", res.status === 409,
            `HTTP ${res.status}`);
        check("and it says what to do instead",
            /administrator|revoke/i.test(res.body.message || ""), res.body.message);

        console.log("\nBut an admin can revoke it");
        res = await api("POST", `/admin/leave/${second}/revoke`,
            { token: admin, body: { remarks: "Cover not available" } });
        check("revoking works", res.status === 200, `HTTP ${res.status}`);
        check("the status says so",
            psql(`SELECT status FROM leave_requests WHERE id=${second}`) === "REVOKED");
        check("both sides are in the audit log",
            Number(psql(`SELECT COUNT(*) FROM activity_logs
                          WHERE activity LIKE 'LEAVE REVOKED%'`)) >= 2);

        console.log("\nTHE POINT: approved leave is Leave, not Absent");
        // Two days in the past, approved, with no attendance at all — which
        // before this feature was indistinguishable from not turning up.
        const leaveFrom = dayFromNow(-3);
        const leaveTo = dayFromNow(-2);
        psql(`INSERT INTO leave_requests
                 (employee_id, leave_type, reason, start_date, end_date,
                  total_days, status, approved_by, approved_at)
              VALUES ('E001','CASUAL','Approved and taken','${leaveFrom}','${leaveTo}',
                      2,'APPROVED','A001', NOW() AT TIME ZONE 'UTC')`);

        const report = await api("GET",
            `/admin/reports/attendance?from=${dayFromNow(-4)}&to=${dayFromNow(-1)}`,
            { token: admin });
        const mine = (report.body.rows || []).find((r) => r.employee_id === "E001");
        check("the report answers", report.status === 200, `HTTP ${report.status}`);
        check("two days are counted as leave", mine && Number(mine.leave_days) === 2,
            JSON.stringify(mine));
        check("and NOT as absence",
            mine && !(mine.absent_dates || []).includes(leaveFrom),
            JSON.stringify(mine?.absent_dates));
        check("the leave dates are listed separately",
            mine && (mine.leave_dates || []).includes(leaveFrom),
            JSON.stringify(mine?.leave_dates));

        // And on the attendance page, where a row exists for the day.
        psql(`INSERT INTO attendance (employee_id, login_time, logout_time)
              VALUES ('E001', '${leaveFrom} 04:00:00', '${leaveFrom} 06:00:00')`);
        const list = await api("GET", "/attendance/all", { token: admin });
        const row = (list.body.data || []).find((r) => r.employee_id === "E001");
        check("an attendance row on a leave day says On Leave",
            row && row.shift_status === "on_leave" && /leave/i.test(row.shift_label),
            JSON.stringify(row && { s: row.shift_status, l: row.shift_label }));
        // The record's own state is a separate question, and answering it
        // has to keep working: this shift was closed, so it is Completed —
        // being on leave that day does not change what happened to the row.
        check("and the record itself still reads Completed",
            row && row.attendance_status === "completed",
            JSON.stringify(row && row.attendance_label));

        console.log("\nThe dashboard counts what needs doing");
        const dash = await api("GET", "/dashboard/summary", { token: admin });
        check("pending leave is on it",
            dash.status === 200 && typeof dash.body.data.pending_leave === "number",
            JSON.stringify(dash.body.data).slice(0, 160));

        server.close();
        await pool.end();
    } finally {
        try { psql(`DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`, "postgres"); } catch (_) {}
        try { fs.rmSync(uploads, { recursive: true, force: true }); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.stdout.write("", () => process.exit(1));
    } else {
        console.log("all leave checks passed");
        process.stdout.write("", () => process.exit(0));
    }
}

main().catch((error) => {
    console.error(error);
    try { psql(`DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`, "postgres"); } catch (_) {}
    process.exit(1);
});
