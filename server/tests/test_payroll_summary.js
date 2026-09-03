/**
 * The month's summary, and the one property a summary cannot be wrong about:
 * ITS PARTS MUST ADD UP TO ITS OWN TOTAL.
 *
 * The summary endpoint answers one question — "what did this month cost, and
 * who lost pay" — and prints a breakdown directly above the total payroll
 * cost. If the breakdown is missing a kind of deduction, the page shows a
 * subtraction that does not reach the figure printed underneath it, and the
 * reader has no way to tell which of the two numbers to believe.
 *
 * That is not hypothetical. The totals were written when a line could only
 * lose money two ways — unpaid leave and absence — and the payslip has since
 * grown two more: the lateness charge and the entered deductions, which is
 * where provident fund, ESI and professional tax land. `GET /admin/payroll`
 * was updated for both and its own comment says why ("Leaving lateness and
 * the entered deductions out of this sum while the lines carried them is how
 * a footer starts disagreeing with the table it is under"). The summary was
 * not, so the same month reported two different deduction totals depending on
 * which screen asked.
 *
 * So this test does not check the totals against numbers written down here —
 * that would only pin today's arithmetic. It checks them against the LINES the
 * same response is built from, and against each other. A summary that cannot
 * be reconciled to its own lines is broken whatever the figures are.
 *
 * Run:  node server/tests/test_payroll_summary.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_paysum_${process.pid}`;
const PORT = 8000 + ((process.pid + 217) % 1000);
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

/** Two-decimal money, the same rounding the server does. */
const money = (n) => Math.round(Number(n) * 100) / 100;

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
    console.log("\nThe payroll summary adds up\n");

    migrate(DB);

    const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
    const seeded = await bcrypt.hash("SuperSecret123", 10);
    psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name,
                                     designation, department, created_at)
              VALUES ('SA001','superadmin','${seeded}','super_admin','Owner',NULL,NULL,'2026-01-01'),
                     ('E001','asha','${seeded}','employee','Asha','Developer','Engineering','2026-01-01'),
                     ('E002','bilal','${seeded}','employee','Bilal','Designer','Design','2026-01-01')`);

    psql(DB, `UPDATE employee_configs
                 SET shift_start='09:00', shift_end='18:00',
                     weekly_offs='7', late_grace_minutes=10
               WHERE employee_id IS NULL`);

    psql(DB, `INSERT INTO employee_salaries
                 (employee_id, gross_monthly, overtime_hourly, effective_from)
              VALUES ('E001', 26000, 200, '2026-01-01'),
                     ('E002', 26000, 150, '2026-01-01')`);

    // June 2026: Sundays on the 7th/14th/21st/28th, so 26 working days.
    // Asha is late past the grace on three mornings; Bilal is never late and
    // misses the 10th entirely, so the month carries an absence too.
    const logins = [];
    for (let d = 1; d <= 30; d += 1) {
        const day = String(d).padStart(2, "0");
        if ([7, 14, 21, 28].includes(d)) continue;
        const utc = [2, 3, 4].includes(d) ? "04:00:00" : "03:30:00";
        logins.push(`('E001', '2026-06-${day} ${utc}', '2026-06-${day} 12:30:00')`);
        if (d !== 10) {
            logins.push(`('E002', '2026-06-${day} 03:30:00', '2026-06-${day} 12:30:00')`);
        }
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

    const token = (await api("POST", "/auth/login",
        { body: { username: "superadmin", password: "SuperSecret123" } })).body.token;

    await api("POST", "/admin/payroll/generate", { token, body: { month: "2026-06" } });

    // Every remaining kind of money on the payslip, so no component of the
    // breakdown is zero — a total that omits a term still looks correct while
    // that term happens to be nothing.
    //
    // Lateness is charged per late day; the entered deduction is the provident
    // fund; and an adjustment is added last because it is the one that moves
    // the net AFTER the line was frozen.
    // The lateness policy is read from app_settings at generation and frozen
    // into the run, so it is set BEFORE the month is generated again.
    psql(DB, `INSERT INTO app_settings (key, value)
              VALUES ('late_deduction_mode','PER_DAY'),
                     ('late_deduction_free_days','1')
              ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`);
    await api("POST", "/admin/payroll/generate", { token, body: { month: "2026-06" } });
    await api("POST", "/admin/payroll/2026-06/deductions", {
        token, body: { employee_id: "E001", kind: "PF", amount: 1800, reason: "Provident fund" },
    });
    await api("POST", "/admin/payroll/2026-06/overtime",
        { token, body: { employee_id: "E002", hours: 4 } });
    await api("POST", "/admin/payroll/2026-06/adjustments",
        { token, body: { employee_id: "E001", kind: "FINE", amount: 200,
                         reason: "Late fine" } });

    // ── the two answers about the same month ────────────────────────────
    const run = await api("GET", "/admin/payroll/2026-06", { token });
    const summary = await api("GET", "/admin/payroll/2026-06/summary", { token });
    check("the summary answers", summary.status === 200 && summary.body.success,
        JSON.stringify(summary.body).slice(0, 200));

    const lines = run.body.lines || [];
    const totals = summary.body.totals || {};
    check("it covers every line the run has",
        Number(totals.employees) === lines.length,
        `${totals.employees} vs ${lines.length} lines`);

    // The lines are the source of truth: they are what the payslips print.
    const sum = (pick) => money(lines.reduce((t, l) => t + Number(pick(l) || 0), 0));
    const expected = {
        gross: sum((l) => l.gross_monthly),
        absent_deduction: sum((l) => l.absent_deduction),
        unpaid_leave_deduction: sum((l) => l.unpaid_deduction),
        late_deduction: sum((l) => l.late_deduction),
        other_deductions: sum((l) => l.other_deductions),
        total_deductions: sum((l) => l.total_deductions),
        overtime_amount: sum((l) => l.overtime_amount),
        adjustments: sum((l) => l.adjustments_total),
        net: sum((l) => l.net_pay),
    };

    // Guard the fixture itself: if the setup above stopped producing one of
    // these, the reconciliation below would pass on a month where the missing
    // term is zero, and prove nothing.
    for (const key of ["late_deduction", "other_deductions", "absent_deduction",
                       "overtime_amount", "adjustments"]) {
        check(`the fixture actually produces ${key}`, expected[key] !== 0,
            `${key} is ${expected[key]}`);
    }

    for (const [key, want] of Object.entries(expected)) {
        if (totals[key] === undefined) {
            check(`totals carry ${key}`, false, "missing from the response");
            continue;
        }
        check(`totals.${key} matches the lines`, money(totals[key]) === want,
            `${totals[key]} vs ${want}`);
    }

    // ── THE ONE THAT MATTERS ────────────────────────────────────────────
    //
    // The breakdown the page prints, reconciled to the total printed under it.
    const reconciled = money(Number(totals.gross || 0)
        - Number(totals.total_deductions || 0)
        + Number(totals.overtime_amount || 0)
        + Number(totals.adjustments || 0));
    check("gross − deductions + overtime + adjustments IS the net payout",
        reconciled === money(totals.net),
        `breakdown reaches ${reconciled}, total says ${money(totals.net)}`);

    // And the same month may not report two different deduction totals
    // depending on which screen asked for it.
    check("the summary's deductions agree with the payroll table's footer",
        money(totals.total_deductions) === money(run.body.totals?.deductions),
        `summary ${totals.total_deductions} vs table ${run.body.totals?.deductions}`);

    // ── the two lists, which the page shows as tables ───────────────────
    check("everyone charged for lateness is listed",
        (summary.body.lateness || []).length
            === lines.filter((l) => Number(l.late_deduction) > 0).length,
        JSON.stringify(summary.body.lateness));
    check("and everyone with an entered deduction",
        (summary.body.deductions || []).length
            === lines.filter((l) => Number(l.other_deductions) > 0).length,
        JSON.stringify(summary.body.deductions));

    // Backward compatibility: the keys the page has always read stay put.
    for (const key of ["leave_deductions", "overtime"]) {
        check(`the existing '${key}' list is still there`,
            Array.isArray(summary.body[key]), typeof summary.body[key]);
    }

    // A month nobody generated is a 404, not an empty summary that reads as
    // "this month cost nothing".
    const missing = await api("GET", "/admin/payroll/2026-05/summary", { token });
    check("an ungenerated month is refused, not summarised as zero",
        missing.status === 404, `${missing.status}`);

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
