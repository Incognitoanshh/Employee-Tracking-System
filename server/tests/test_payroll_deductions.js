/**
 * Payroll's second half: lateness, deductions, deleting a draft, and reports.
 *
 * WHAT THIS IS GUARDING, in one sentence: that a payslip's parts always add up
 * to its total, no matter which of them somebody edits.
 *
 * The dangerous operation is editing one component of a line that is already
 * frozen. The obvious way to do it — put the stored line back through the
 * function that built it — quietly returns every component the caller did not
 * mention to zero. An administrator types an overtime figure, and a provident
 * fund deduction disappears from the payslip with nothing logged and nothing
 * failing. That is tested here directly, because it is invisible everywhere
 * else.
 *
 * The other rule under test is the boundary between the two ways money moves:
 *
 *   an ADJUSTMENT is a decision about one person in one month, may be added
 *   after finalisation, and is how a finalised month is corrected;
 *
 *   a DEDUCTION is part of the payslip itself — provident fund, professional
 *   tax — so it may only be entered while the month is a draft. Adding one
 *   afterwards would rewrite what somebody was already told they were paid.
 *
 * Run:  node server/tests/test_payroll_deductions.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_payded_${process.pid}`;
const PORT = 8000 + ((process.pid + 733) % 1000);
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

async function api(method, route, { token, body, raw } = {}) {
    const response = await fetch(`${BASE}${route}`, {
        method,
        headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (raw) return { status: response.status, text: await response.text() };
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return { status: response.status, body: payload };
}

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log("\nPayroll: lateness, deductions, drafts and reports\n");

    migrate(DB);

    const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
    const seeded = await bcrypt.hash("SuperSecret123", 10);
    psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name,
                                     designation, department, created_at)
              VALUES ('SA001','superadmin','${seeded}','super_admin','Owner',NULL,NULL,'2026-01-01'),
                     ('E001','asha','${seeded}','employee','Asha','Developer','Engineering','2026-01-01'),
                     ('E002','bilal','${seeded}','employee','Bilal','Designer','Design','2026-01-01')`);

    // A 09:00-18:00 shift for everybody, ten minutes' grace, Sunday off.
    psql(DB, `UPDATE employee_configs
                 SET shift_start='09:00', shift_end='18:00',
                     weekly_offs='7', late_grace_minutes=10
               WHERE employee_id IS NULL`);

    psql(DB, `INSERT INTO employee_salaries
                 (employee_id, gross_monthly, overtime_hourly, effective_from)
              VALUES ('E001', 26000, 200, '2026-01-01'),
                     ('E002', 26000, 150, '2026-01-01')`);

    // June 2026: 30 days, Sundays are the 7th, 14th, 21st, 28th → 26 working
    // days. Asha signs in on every one of them; three of those mornings she is
    // late past the grace period.
    //
    // Stored UTC, IST is +5:30. 03:30Z = 09:00 IST exactly.
    const logins = [];
    for (let d = 1; d <= 30; d += 1) {
        const day = String(d).padStart(2, "0");
        if ([7, 14, 21, 28].includes(d)) continue;          // Sundays
        // 09:00 normally; 09:30 on the 2nd, 3rd and 4th — half an hour past
        // the start, and so well past the ten-minute grace.
        const utc = [2, 3, 4].includes(d) ? "04:00:00" : "03:30:00";
        logins.push(`('E001', '2026-06-${day} ${utc}', '2026-06-${day} 12:30:00')`);
        logins.push(`('E002', '2026-06-${day} 03:30:00', '2026-06-${day} 12:30:00')`);
    }
    psql(DB, `INSERT INTO attendance (employee_id, login_time, logout_time)
              VALUES ${logins.join(",")}`);

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

    const login = await api("POST", "/auth/login",
        { body: { username: "superadmin", password: "SuperSecret123" } });
    const token = login.body.token;

    const lineFor = async (id, month = "2026-06") => {
        const res = await api("GET", `/admin/payroll/${month}`, { token });
        return (res.body.lines || []).find((l) => l.employee_id === id);
    };

    // ── lateness is recorded, and costs nothing by default ──────────────
    let res = await api("POST", "/admin/payroll/generate",
        { token, body: { month: "2026-06" } });
    check("the month generates", res.status === 200 && res.body.success,
        JSON.stringify(res.body));

    let asha = await lineFor("E001");
    check("three late mornings are counted", Number(asha?.late_days) === 3,
        `late_days=${asha?.late_days}`);
    // THIRTY MINUTES EACH, NOT TWENTY. The grace period decides WHETHER a
    // morning counts as late, not how late it was: someone who arrives at
    // 09:30 on a 09:00 shift is thirty minutes late, and the ten-minute grace
    // is why 09:08 would not have counted at all. This is the same figure the
    // Attendance table prints as "Late 30m", and it has to be — a payslip and
    // an attendance page disagreeing about the same morning is unanswerable.
    check("and the minutes with them — thirty past the start, three times",
        Number(asha?.late_minutes) === 90, `late_minutes=${asha?.late_minutes}`);
    check("but lateness costs nothing until somebody says it does",
        Number(asha?.late_deduction) === 0, `late_deduction=${asha?.late_deduction}`);
    check("so a full month present is paid in full",
        Number(asha?.net_before_adjustments) === 26000,
        `net=${asha?.net_before_adjustments}`);

    // The colleague who was never late must not pick up somebody else's
    // minutes — the lateness is gathered per person, per day.
    const bilal = await lineFor("E002");
    check("a punctual colleague has no late days",
        Number(bilal?.late_days) === 0 && Number(bilal?.late_minutes) === 0,
        `${bilal?.late_days} days, ${bilal?.late_minutes} minutes`);

    // ── deductions ──────────────────────────────────────────────────────
    res = await api("POST", "/admin/payroll/2026-06/deductions", {
        token,
        body: { employee_id: "E001", kind: "PF", amount: 1800, reason: "Provident fund" },
    });
    check("a provident fund deduction is accepted", res.status === 201,
        JSON.stringify(res.body));

    asha = await lineFor("E001");
    check("the line's frozen total moves with it",
        Number(asha?.other_deductions) === 1800, `other=${asha?.other_deductions}`);
    check("and the net comes down by exactly that",
        Number(asha?.net_before_adjustments) === 24200,
        `net=${asha?.net_before_adjustments}`);
    check("the itemised deductions add up to the frozen figure",
        Number(asha?.entered_deductions_total) === Number(asha?.other_deductions),
        `${asha?.entered_deductions_total} vs ${asha?.other_deductions}`);

    res = await api("POST", "/admin/payroll/2026-06/deductions", {
        token,
        body: { employee_id: "E001", kind: "PF", amount: -500, reason: "negative" },
    });
    check("a negative deduction is refused, not quietly paid",
        res.status === 400, `${res.status} ${res.body.message}`);

    res = await api("POST", "/admin/payroll/2026-06/deductions", {
        token, body: { employee_id: "E001", kind: "PF", amount: 500, reason: "   " },
    });
    check("a deduction with no reason is refused", res.status === 400,
        `${res.status} ${res.body.message}`);

    // ── THE ONE THAT MATTERS: editing one component keeps the others ────
    res = await api("POST", "/admin/payroll/2026-06/overtime",
        { token, body: { employee_id: "E001", hours: 10 } });
    check("overtime is accepted", res.status === 200, JSON.stringify(res.body));

    asha = await lineFor("E001");
    check("entering overtime does not wipe the provident fund",
        Number(asha?.other_deductions) === 1800,
        `other_deductions became ${asha?.other_deductions}`);
    check("and the net is gross − deductions + overtime, all of them",
        Number(asha?.net_before_adjustments) === 26000 - 1800 + 2000,
        `net=${asha?.net_before_adjustments}, expected 26200`);

    // ── a rebuild keeps what was entered by hand ────────────────────────
    res = await api("POST", "/admin/payroll/generate",
        { token, body: { month: "2026-06" } });
    check("the month regenerates", res.status === 200, JSON.stringify(res.body));
    asha = await lineFor("E001");
    check("regenerating carries the deduction forward",
        Number(asha?.other_deductions) === 1800, `other=${asha?.other_deductions}`);
    check("and the lateness is recomputed, not doubled",
        Number(asha?.late_minutes) === 90, `late_minutes=${asha?.late_minutes}`);
    // AND THE OVERTIME THAT WAS ENTERED. This used to be reset to nothing on
    // every rebuild, on the grounds that overtime is entered by hand after
    // generation — which it still is. What changed is that a draft is now
    // rebuilt on its own whenever somebody's salary is saved, so a rebuild
    // that threw overtime away would erase every approved hour in the month
    // the moment anybody's pay was corrected, with nothing logged. Entered
    // overtime is kept exactly as entered deductions and adjustments are.
    check("and the overtime entered by hand survives the rebuild",
        Number(asha?.overtime_hours) === 10 && Number(asha?.overtime_amount) === 2000,
        `hours=${asha?.overtime_hours} amount=${asha?.overtime_amount}`);

    // ── removing one ────────────────────────────────────────────────────
    const list = await api("GET", "/admin/payroll/2026-06", { token });
    const pf = (list.body.lines.find((l) => l.employee_id === "E001").deductions || [])[0];
    check("the deduction is itemised on the line", Boolean(pf), JSON.stringify(pf));
    res = await api("DELETE", `/admin/payroll/deductions/${pf.id}`, { token });
    check("it can be removed while the month is a draft", res.status === 200,
        JSON.stringify(res.body));
    asha = await lineFor("E001");
    // Gross, plus the ten hours of overtime that the rebuild above kept.
    check("and the net goes back up by exactly the deduction removed",
        Number(asha?.other_deductions) === 0
        && Number(asha?.net_before_adjustments) === 26000 + 2000,
        `other=${asha?.other_deductions} net=${asha?.net_before_adjustments}`
        + ", expected 28000");

    // ── reports ─────────────────────────────────────────────────────────
    res = await api("GET", "/admin/payroll/2026-06/report?group=employee", { token });
    check("a report by employee lists everybody",
        res.status === 200 && res.body.data?.length === 2,
        `${res.status} ${res.body.data?.length} rows`);
    check("and carries the lateness onto it",
        res.body.data?.find((r) => r.employee_id === "E001")?.late_minutes === 90,
        JSON.stringify(res.body.data?.[0]));

    res = await api("GET", "/admin/payroll/2026-06/report?group=department", { token });
    check("a report by department groups them",
        res.status === 200 && res.body.data?.length === 2,
        JSON.stringify(res.body.data));
    check("and names the department rather than the job title",
        res.body.data?.some((r) => r.department === "Engineering"),
        JSON.stringify(res.body.data));

    const csv = await api("GET", "/admin/payroll/2026-06/report?format=csv",
        { token, raw: true });
    check("the CSV comes back as a file", csv.status === 200
        && csv.text.includes("employee_id") && csv.text.includes("E001"),
        csv.text.slice(0, 120));
    check("every field is quoted, so a comma in a name cannot shift a column",
        csv.text.split("\r\n")[1]?.startsWith('"E001"'),
        csv.text.split("\r\n")[1]?.slice(0, 60));

    // ── salary remarks ──────────────────────────────────────────────────
    res = await api("POST", "/admin/payroll/salaries", {
        token,
        body: { employee_id: "E001", gross_monthly: 30000, overtime_hourly: 220,
                effective_from: "2026-07-01", remarks: "Annual review, promoted" },
    });
    check("a salary can be set with a reason", res.status === 200,
        JSON.stringify(res.body));

    res = await api("GET", "/admin/payroll/salaries/E001", { token });
    check("and the history keeps it",
        res.body.data?.[0]?.remarks === "Annual review, promoted",
        JSON.stringify(res.body.data?.[0]));
    check("the history is newest first, with both versions",
        res.body.data?.length === 2
        && res.body.data[0].effective_from === "2026-07-01",
        JSON.stringify(res.body.data?.map((r) => r.effective_from)));

    // Editing the figure without saying anything must not erase the reason.
    await api("POST", "/admin/payroll/salaries", {
        token,
        body: { employee_id: "E001", gross_monthly: 31000, overtime_hourly: 220,
                effective_from: "2026-07-01" },
    });
    res = await api("GET", "/admin/payroll/salaries/E001", { token });
    check("correcting the figure keeps the reason that was already there",
        res.body.data?.[0]?.remarks === "Annual review, promoted",
        JSON.stringify(res.body.data?.[0]?.remarks));

    // ── deleting a draft ────────────────────────────────────────────────
    res = await api("POST", "/admin/payroll/generate",
        { token, body: { month: "2026-05" } });
    check("a second month generates", res.status === 200, JSON.stringify(res.body));
    res = await api("DELETE", "/admin/payroll/2026-05", { token });
    check("a draft can be thrown away", res.status === 200, JSON.stringify(res.body));
    res = await api("GET", "/admin/payroll/2026-05", { token });
    check("and it is gone", res.body.run === null, JSON.stringify(res.body.run));

    // ── finalising closes the door ──────────────────────────────────────
    res = await api("POST", "/admin/payroll/2026-06/finalize", { token });
    check("the month finalises", res.status === 200, JSON.stringify(res.body));

    res = await api("POST", "/admin/payroll/2026-06/deductions", {
        token, body: { employee_id: "E001", kind: "ESI", amount: 200, reason: "late ESI" },
    });
    check("a deduction on a finalised month is refused", res.status === 409,
        `${res.status} ${res.body.message}`);

    res = await api("DELETE", "/admin/payroll/2026-06", { token });
    check("and a finalised month cannot be deleted", res.status === 409,
        `${res.status} ${res.body.message}`);

    // An adjustment still can — that is the escape hatch finalising leaves.
    res = await api("POST", "/admin/payroll/2026-06/adjustments", {
        token,
        body: { employee_id: "E001", kind: "FINE", amount: 200, reason: "ESI, agreed" },
    });
    check("but an adjustment still may be added, which is the way to correct it",
        res.status === 201, `${res.status} ${res.body.message}`);

    // ── an employee may not do any of this ──────────────────────────────
    const asAsha = (await api("POST", "/auth/login",
        { body: { username: "asha", password: "SuperSecret123" } })).body.token;
    res = await api("POST", "/admin/payroll/2026-06/deductions", {
        token: asAsha,
        body: { employee_id: "E002", kind: "PF", amount: 100, reason: "no" },
    });
    check("an employee cannot deduct from a colleague", res.status === 403,
        `${res.status}`);
    res = await api("GET", "/admin/payroll/2026-06/report", { token: asAsha });
    check("nor read the payroll report", res.status === 403, `${res.status}`);
    res = await api("DELETE", "/admin/payroll/2026-06", { token: asAsha });
    check("nor delete a month", res.status === 403, `${res.status}`);

    // ── what an employee may see of their own pay ───────────────────────
    res = await api("GET", "/payroll/mine/salary", { token: asAsha });
    check("an employee can read their own salary",
        res.status === 200 && Number(res.body.salary?.gross_monthly) === 31000,
        JSON.stringify(res.body.salary));
    check("with the date it took effect and the overtime rate",
        res.body.salary?.effective_from === "2026-07-01"
        && Number(res.body.salary?.overtime_hourly) === 220,
        JSON.stringify(res.body.salary));
    check("and their latest FINALISED payslip, not a draft",
        res.body.latest_payroll?.month === "2026-06",
        JSON.stringify(res.body.latest_payroll));
    // The 200 fine added above comes off the net the employee is shown — the
    // net that also carries the ten overtime hours kept through the rebuild.
    check("the payslip's net includes the adjustments made to it",
        Number(res.body.latest_payroll?.net_pay) === 26000 + 2000 - 200,
        `${JSON.stringify(res.body.latest_payroll?.net_pay)}, expected 27800`);

    // THE ROUTE TAKES NO EMPLOYEE ID, so there is nothing to change to a
    // colleague's. Asked as Bilal, it answers about Bilal.
    const asBilal = (await api("POST", "/auth/login",
        { body: { username: "bilal", password: "SuperSecret123" } })).body.token;
    res = await api("GET", "/payroll/mine/salary", { token: asBilal });
    check("and it answers about whoever asked, never a colleague",
        Number(res.body.salary?.gross_monthly) === 26000,
        JSON.stringify(res.body.salary));

    await pool.end();
    server.close();
    console.log(`\n${failures ? `${failures} FAILED` : "ALL PASS"}\n`);
    try { execFileSync("dropdb", ["--if-exists", DB]); } catch (_) {}
    process.exit(failures ? 1 : 0);
}

main().catch((error) => {
    console.error(error);
    try { execFileSync("dropdb", ["--if-exists", DB]); } catch (_) {}
    process.exit(1);
});
