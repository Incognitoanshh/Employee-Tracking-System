/**
 * Payroll end to end: salaries, a month, and what a payslip says.
 *
 * The arithmetic itself is checked in test_payroll_math.js, against sums
 * anybody can do by hand. THIS file checks the things only a real month can
 * show: that attendance, approved leave and unapproved absence turn into the
 * right days; that a finalised month stops moving; and that an employee can
 * reach their own payslip and nobody else's.
 *
 * Run:  node server/tests/test_payroll.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { migrate } = require("./_migrate");

const DB = `ets_payroll_${process.pid}`;
const PORT = 8000 + ((process.pid + 631) % 1000);
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

async function api(method, route, { token, body, raw } = {}) {
    const response = await fetch(`${BASE}${route}`, {
        method,
        headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        ...(body && method !== "GET" ? { body: JSON.stringify(body) } : {}),
    });
    if (raw) return { status: response.status, text: await response.text() };
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return { status: response.status, body: payload };
}

const login = async (username, device) =>
    (await api("POST", "/auth/login",
        { body: { username, password: PASSWORD, device_id: device } })).body.token;

const near = (a, b) => Math.abs(Number(a) - Number(b)) < 0.02;

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    const uploads = fs.mkdtempSync(path.join(os.tmpdir(), "ets_payroll_"));
    console.log(`Payroll (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(`INSERT INTO employees (employee_id, username, password, role,
                                     full_name, email, created_at)
              VALUES ('A001','admin1','${hash}','admin','Priya Nair','p@x.test',
                      '2025-01-01'),
                     ('E001','rajesh','${hash}','employee','Rajesh Kumar','r@x.test',
                      '2025-01-01'),
                     ('E002','sneha','${hash}','employee','Sneha Iyer',NULL,
                      '2025-01-01')`);
        // No weekly offs and no holidays, so a 30-day month has 30 working
        // days and every figure below can be checked by hand.
        psql(`UPDATE employee_configs SET weekly_offs = '' WHERE employee_id IS NULL`);

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

        const MONTH = "2026-06";          // 30 days
        console.log("Salaries");
        let res = await api("POST", "/admin/payroll/salaries", { token: rajesh, body: {
            employee_id: "E001", gross_monthly: 30000 } });
        check("an employee cannot set a salary", res.status === 403, `HTTP ${res.status}`);

        res = await api("POST", "/admin/payroll/salaries", { token: admin, body: {
            employee_id: "E001", gross_monthly: 30000, overtime_hourly: 200,
            effective_from: "2026-01-01" } });
        check("an admin can", res.status === 200, `HTTP ${res.status}`);
        await api("POST", "/admin/payroll/salaries", { token: admin, body: {
            employee_id: "E002", gross_monthly: 60000, effective_from: "2026-01-01" } });

        for (const [what, body] of [
            ["a negative salary", { employee_id: "E001", gross_monthly: -1 }],
            ["a salary that is not a number", { employee_id: "E001", gross_monthly: "lots" }],
            ["nobody in particular", { gross_monthly: 100 }],
        ]) {
            res = await api("POST", "/admin/payroll/salaries", { token: admin, body });
            check(`${what} is refused`, res.status === 400, `HTTP ${res.status}`);
        }

        // A RISE LATER MUST NOT REWRITE AN EARLIER MONTH.
        await api("POST", "/admin/payroll/salaries", { token: admin, body: {
            employee_id: "E001", gross_monthly: 36000, effective_from: "2026-07-01" } });

        console.log("\nA month, from real attendance and leave");
        // June: Rajesh present on 20 days, 2 days approved sick (paid),
        // 3 days approved unpaid, and the remaining 5 unexplained.
        for (let d = 1; d <= 20; d += 1) {
            const day = String(d).padStart(2, "0");
            psql(`INSERT INTO attendance (employee_id, login_time, logout_time)
                  VALUES ('E001','2026-06-${day} 04:00:00','2026-06-${day} 12:00:00')`);
        }
        psql(`INSERT INTO leave_requests (employee_id, leave_type, reason, start_date,
                                          end_date, total_days, status, approved_by)
              VALUES ('E001','SICK','flu','2026-06-21','2026-06-22',2,'APPROVED','A001'),
                     ('E001','UNPAID','personal','2026-06-23','2026-06-25',3,'APPROVED','A001')`);

        res = await api("POST", "/admin/payroll/generate",
            { token: admin, body: { month: MONTH } });
        check("the month generates", res.status === 200, JSON.stringify(res.body).slice(0, 120));

        res = await api("GET", `/admin/payroll/${MONTH}`, { token: admin });
        const line = (res.body.lines || []).find((l) => l.employee_id === "E001");
        check("thirty working days", near(line.working_days, 30), String(line?.working_days));
        check("twenty present", near(line.present_days, 20), String(line?.present_days));
        check("two days of paid leave", near(line.paid_leave_days, 2),
            String(line?.paid_leave_days));
        check("three unpaid", near(line.unpaid_leave_days, 3),
            String(line?.unpaid_leave_days));
        check("and five unexplained days are absence",
            near(line.absent_days, 5), String(line?.absent_days));

        // 30000 / 30 = 1000 a day. 3 unpaid + 5 absent = 8 days = 8000 off.
        check("the day rate", near(line.per_day, 1000), String(line?.per_day));
        check("unpaid leave costs three days", near(line.unpaid_deduction, 3000),
            String(line?.unpaid_deduction));
        check("absence costs five", near(line.absent_deduction, 5000),
            String(line?.absent_deduction));
        check("and the net is what is left", near(line.net_pay, 22000),
            String(line?.net_pay));
        check("SICK LEAVE COST NOTHING — the owner's rule",
            near(Number(line.gross_monthly) - Number(line.unpaid_deduction)
                 - Number(line.absent_deduction), 22000));

        check("the June salary was used, not July's rise",
            near(line.gross_monthly, 30000), String(line?.gross_monthly));

        console.log("\nOvertime, entered by hand");
        res = await api("POST", `/admin/payroll/${MONTH}/overtime`, { token: admin, body: {
            employee_id: "E001", hours: 6 } });
        check("hours are accepted", res.status === 200, `HTTP ${res.status}`);
        res = await api("GET", `/admin/payroll/${MONTH}`, { token: admin });
        const withOvertime = res.body.lines.find((l) => l.employee_id === "E001");
        check("six hours at 200 is 1200", near(withOvertime.overtime_amount, 1200),
            String(withOvertime?.overtime_amount));
        check("and the net moves with it", near(withOvertime.net_pay, 23200),
            String(withOvertime?.net_pay));
        res = await api("POST", `/admin/payroll/${MONTH}/overtime`, { token: admin, body: {
            employee_id: "E001", hours: -3 } });
        check("negative hours are refused", res.status === 400, `HTTP ${res.status}`);

        console.log("\nAdjustments carry their sign and their reason");
        res = await api("POST", `/admin/payroll/${MONTH}/adjustments`, { token: admin, body: {
            employee_id: "E001", kind: "FINE", amount: 500 } });
        check("an adjustment without a reason is refused", res.status === 400,
            `HTTP ${res.status}`);
        res = await api("POST", `/admin/payroll/${MONTH}/adjustments`, { token: admin, body: {
            employee_id: "E001", kind: "FINE", amount: 500, reason: "Late three times" } });
        check("with one, it is accepted", res.status === 201, `HTTP ${res.status}`);
        check("A FINE ENTERED AS +500 STILL TAKES MONEY AWAY",
            Number(res.body.adjustment.amount) === -500,
            String(res.body.adjustment?.amount));
        await api("POST", `/admin/payroll/${MONTH}/adjustments`, { token: admin, body: {
            employee_id: "E001", kind: "BONUS", amount: 2000, reason: "Shipped on time" } });

        res = await api("GET", `/admin/payroll/${MONTH}`, { token: admin });
        const adjusted = res.body.lines.find((l) => l.employee_id === "E001");
        check("they add up", near(adjusted.adjustments_total, 1500),
            String(adjusted?.adjustments_total));
        check("and move the net", near(adjusted.net_pay, 24700), String(adjusted?.net_pay));

        console.log("\nSomebody with no attendance at all");
        const sneha_line = res.body.lines.find((l) => l.employee_id === "E002");
        check("is absent for the whole month", near(sneha_line.absent_days, 30),
            String(sneha_line?.absent_days));
        check("and is paid nothing, not less than nothing",
            near(sneha_line.net_pay, 0), String(sneha_line?.net_pay));

        console.log("\nFinalising stops the month moving");
        res = await api("GET", "/payroll/mine", { token: rajesh });
        check("a draft is not shown to the employee", (res.body.data || []).length === 0,
            JSON.stringify(res.body.data));

        res = await api("POST", `/admin/payroll/${MONTH}/finalize`, { token: rajesh });
        check("an employee cannot finalise", res.status === 403, `HTTP ${res.status}`);
        res = await api("POST", `/admin/payroll/${MONTH}/finalize`, { token: admin });
        check("an admin can", res.status === 200, `HTTP ${res.status}`);
        check("it is recorded against them",
            psql(`SELECT finalized_by FROM payroll_runs WHERE month='2026-06-01'`) === "A001");

        res = await api("POST", "/admin/payroll/generate",
            { token: admin, body: { month: MONTH } });
        check("a finalised month cannot be regenerated", res.status === 409,
            `HTTP ${res.status}`);
        res = await api("POST", `/admin/payroll/${MONTH}/overtime`, { token: admin, body: {
            employee_id: "E001", hours: 20 } });
        check("nor can its overtime be changed", res.status === 409, `HTTP ${res.status}`);

        console.log("\nBut it can still be corrected — by adjustment");
        res = await api("POST", `/admin/payroll/${MONTH}/adjustments`, { token: admin, body: {
            employee_id: "E001", kind: "INCENTIVE", amount: 1000,
            reason: "Overtime approved after finalisation" } });
        check("an adjustment is still allowed", res.status === 201, `HTTP ${res.status}`);
        res = await api("GET", `/admin/payroll/${MONTH}`, { token: admin });
        const corrected = res.body.lines.find((l) => l.employee_id === "E001");
        check("and it moves the net", near(corrected.net_pay, 25700),
            String(corrected?.net_pay));
        check("while the frozen figure is untouched",
            near(corrected.net_before_adjustments, 23200),
            String(corrected?.net_before_adjustments));

        console.log("\nThe employee's own payslip, and nobody else's");
        res = await api("GET", "/payroll/mine", { token: rajesh });
        check("it appears once finalised", (res.body.data || []).length === 1,
            JSON.stringify(res.body.data).slice(0, 120));
        check("with the same net the admin sees",
            near(res.body.data[0].net_pay, 25700), String(res.body.data[0]?.net_pay));

        let page = await api("GET", `/payroll/payslip/${MONTH}`, { token: rajesh, raw: true });
        check("the payslip renders", page.status === 200 && page.text.includes("NET PAY"),
            `HTTP ${page.status}`);
        check("it names the person and the month",
            page.text.includes("Rajesh Kumar") && page.text.includes("2026-06"));
        check("it shows the days it was built from",
            page.text.includes("Working days") && page.text.includes("Absent"));
        check("and every adjustment, with its reason",
            page.text.includes("Late three times") && page.text.includes("Shipped on time"));

        page = await api("GET", `/payroll/payslip/${MONTH}?employee_id=E001`,
            { token: sneha, raw: true });
        check("SOMEBODY ELSE'S PAYSLIP IS REFUSED", page.status === 403,
            `HTTP ${page.status}`);
        page = await api("GET", `/payroll/payslip/${MONTH}?employee_id=E001`,
            { token: admin, raw: true });
        check("an admin may read it", page.status === 200, `HTTP ${page.status}`);

        console.log("\nA name cannot become markup in a payslip");
        psql(`UPDATE employees SET full_name = '<script>x</script>' WHERE employee_id='E001'`);
        page = await api("GET", `/payroll/payslip/${MONTH}`, { token: rajesh, raw: true });
        check("the script tag is escaped",
            !page.text.includes("<script>x</script>") && page.text.includes("&lt;script&gt;"));
        psql(`UPDATE employees SET full_name = 'Rajesh Kumar' WHERE employee_id='E001'`);

        console.log("\nThe reports");
        res = await api("GET", `/admin/payroll/${MONTH}/summary`, { token: admin });
        check("the summary answers", res.status === 200, `HTTP ${res.status}`);
        check("it totals the payout",
            near(res.body.totals.net, 25700 + 0), JSON.stringify(res.body.totals));
        check("it lists who lost pay to unpaid leave",
            (res.body.leave_deductions || []).some((r) => r.employee_id === "E001"),
            JSON.stringify(res.body.leave_deductions));
        check("and who was paid overtime",
            (res.body.overtime || []).some((r) => r.employee_id === "E001"),
            JSON.stringify(res.body.overtime));

        res = await api("GET", "/admin/payroll", { token: admin });
        check("the history lists the month", (res.body.data || []).length === 1,
            JSON.stringify(res.body.data).slice(0, 120));

        console.log("\nWhat a bad month name does");
        for (const bad of ["2026-13", "june", "2026", "2026-6"]) {
            res = await api("POST", "/admin/payroll/generate",
                { token: admin, body: { month: bad } });
            check(`"${bad}" is refused`, res.status === 400, `HTTP ${res.status}`);
        }

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
        console.log("all payroll checks passed");
        process.stdout.write("", () => process.exit(0));
    }
}

main().catch((error) => {
    console.error(error);
    try { psql(`DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`, "postgres"); } catch (_) {}
    process.exit(1);
});
