/**
 * A payslip that shows what its gross is made of — and goes on showing the
 * SAME thing after the salary behind it changes.
 *
 * ── THE TRAP THIS FILE EXISTS FOR ───────────────────────────────────────
 *
 * The obvious way to put components on a payslip is a foreign key from the
 * line to the salary version it was built from. It is wrong, and it fails
 * quietly, on finalised months:
 *
 *     setSalary is INSERT ... ON CONFLICT (employee_id, effective_from)
 *     DO UPDATE, and it then DELETEs that salary's components and writes the
 *     new ones.
 *
 * So correcting a salary on the same effective date keeps the same salary id
 * and rewrites its split underneath every payslip pointing at it. Those
 * payslips keep their own frozen gross and acquire somebody else's parts —
 * and the parts stop adding up to the total printed directly above them.
 *
 * The components are therefore COPIED onto the line at generation, like every
 * other figure on it. The check that matters below is the one that changes a
 * salary after finalising and reads the payslip again.
 *
 * ── AND THE DEDUCTIONS THE PAYSLIP WAS NOT PRINTING ─────────────────────
 *
 * The printable payslip listed two deductions — absence and unpaid leave —
 * while NET PAY underneath was the frozen net, which already had the lateness
 * charge and the provident fund taken out of it. An employee's own payslip
 * showed a net ₹1,800 lower than its own arithmetic with nothing saying why.
 * Every deduction is printed now, and the sum of what is printed is asserted
 * against the frozen total.
 *
 * Run:  node server/tests/test_payslip_components.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_payslip_${process.pid}`;
const PORT = 8000 + ((process.pid + 619) % 1000);
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

const money = (n) => Math.round(Number(n) * 100) / 100;

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
    console.log("\nA payslip, and what its gross is made of\n");

    migrate(DB);

    const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
    const seeded = await bcrypt.hash("SuperSecret123", 10);
    psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name,
                                     designation, department, created_at)
              VALUES ('SA001','superadmin','${seeded}','super_admin','Owner',NULL,NULL,'2026-01-01'),
                     ('E001','asha','${seeded}','employee','Asha','Developer','Engineering','2026-01-01')`);

    psql(DB, `UPDATE employee_configs
                 SET shift_start='09:00', shift_end='18:00',
                     weekly_offs='7', late_grace_minutes=10
               WHERE employee_id IS NULL`);

    // Present every working day, late past the grace on three mornings.
    const logins = [];
    for (let d = 1; d <= 30; d += 1) {
        const day = String(d).padStart(2, "0");
        if ([7, 14, 21, 28].includes(d)) continue;
        const utc = [2, 3, 4].includes(d) ? "04:00:00" : "03:30:00";
        logins.push(`('E001', '2026-06-${day} ${utc}', '2026-06-${day} 12:30:00')`);
    }
    psql(DB, `INSERT INTO attendance (employee_id, login_time, logout_time)
              VALUES ${logins.join(",")}`);
    psql(DB, `INSERT INTO app_settings (key, value)
              VALUES ('late_deduction_mode','PER_DAY'),
                     ('late_deduction_free_days','1')
              ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`);

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

    const lineFor = async (month = "2026-06") => {
        const res = await api("GET", `/admin/payroll/${month}`, { token });
        return (res.body.lines || []).find((l) => l.employee_id === "E001");
    };

    // ── a month with no CTC behind it ───────────────────────────────────
    //
    // FIRST, because it is the state every existing month is in. A salary
    // entered as a monthly gross has no split, and the payslip has to say so
    // rather than invent one.
    psql(DB, `INSERT INTO employee_salaries
                 (employee_id, gross_monthly, overtime_hourly, effective_from)
              VALUES ('E001', 26000, 200, '2026-01-01')`);
    await api("POST", "/admin/payroll/generate", { token, body: { month: "2026-06" } });

    let line = await lineFor();
    check("a salary with no CTC produces a line with no components",
        Array.isArray(line?.components) && line.components.length === 0,
        JSON.stringify(line?.components));
    check("and its total is null, not zero — nothing recorded is not nothing",
        line?.components_total === null, String(line?.components_total));

    let slip = await api("GET", "/payroll/payslip/2026-06?employee_id=E001",
        { token, raw: true });
    check("the payslip says the breakdown was not recorded",
        slip.text.includes("not recorded for this month"),
        slip.text.includes("EARNINGS") ? "no notice" : "no payslip");

    // ── now a salary that IS a CTC ──────────────────────────────────────
    let res = await api("POST", "/admin/payroll/salaries", {
        token,
        body: { employee_id: "E001", ctc_annual: 600000, overtime_hourly: 200,
                effective_from: "2026-01-01", epf_enabled: true },
    });
    check("a CTC salary is accepted", res.status === 200 || res.status === 201,
        `${res.status} ${JSON.stringify(res.body).slice(0, 140)}`);

    await api("POST", "/admin/payroll/generate", { token, body: { month: "2026-06" } });
    line = await lineFor();

    check("the line now carries its components",
        (line?.components || []).length === 5,
        JSON.stringify((line?.components || []).map((c) => c.name)));
    check("beginning with Basic",
        line?.components?.[0]?.name === "Basic", line?.components?.[0]?.name);
    check("each one says how it was worked out, not just what it came to",
        line.components.every((c) => c.rule && c.rule.length > 0),
        JSON.stringify(line.components.map((c) => c.rule)));

    // THE PARTS ADD BACK TO THE WHOLE. A payslip whose components do not sum
    // to its gross is a payslip somebody has to explain.
    check("the parts add up to the gross printed above them",
        money(line.components_total) === money(line.gross_monthly),
        `${line.components_total} vs ${line.gross_monthly}`);

    // ── THE ONE THAT MATTERS ────────────────────────────────────────────
    //
    // Finalise, then correct the salary ON THE SAME EFFECTIVE DATE — which is
    // the case that rewrites salary_components under the same id.
    await api("POST", "/admin/payroll/2026-06/finalize", { token });
    const frozen = await lineFor();
    check("the month finalises with its components",
        (frozen?.components || []).length === 5,
        String((frozen?.components || []).length));
    const frozenBasic = frozen.components.find((c) => c.name === "Basic").monthly;

    res = await api("POST", "/admin/payroll/salaries", {
        token,
        body: { employee_id: "E001", ctc_annual: 1200000, overtime_hourly: 200,
                effective_from: "2026-01-01", epf_enabled: true },
    });
    check("the salary is corrected in place, on the same effective date",
        res.status === 200 || res.status === 201, String(res.status));
    // Proof the trap is real: the SALARY's split really did change underneath.
    const salaryBasic = Number(psql(DB,
        `SELECT monthly FROM salary_components c
           JOIN employee_salaries s ON s.id = c.salary_id
          WHERE s.employee_id = 'E001' AND c.name = 'Basic'`));
    check("  …and the salary's own split really did change",
        salaryBasic !== Number(frozenBasic),
        `salary Basic ${salaryBasic}, payslip Basic ${frozenBasic}`);

    const after = await lineFor();
    check("the FINALISED payslip keeps the components it was frozen with",
        Number(after.components.find((c) => c.name === "Basic").monthly)
            === Number(frozenBasic),
        `was ${frozenBasic}, now ${after.components.find((c) => c.name === "Basic").monthly}`);
    check("its gross is unchanged too",
        money(after.gross_monthly) === money(frozen.gross_monthly),
        `${frozen.gross_monthly} -> ${after.gross_monthly}`);
    check("and the parts still add up to it",
        money(after.components_total) === money(after.gross_monthly),
        `${after.components_total} vs ${after.gross_monthly}`);

    // ── the printable payslip ───────────────────────────────────────────
    slip = await api("GET", "/payroll/payslip/2026-06?employee_id=E001",
        { token, raw: true });
    check("the payslip prints every component by name",
        ["Basic", "DA", "House Rent Allowance", "Conveyance Allowance",
         "Fixed Allowance"].every((name) => slip.text.includes(name)),
        "a component is missing from the sheet");
    check("and no longer claims the breakdown is unknown",
        !slip.text.includes("not recorded for this month"));

    // ── the deductions the payslip used to leave off ────────────────────
    await api("POST", "/admin/payroll/2026-06/deductions", {
        token, body: { employee_id: "E001", kind: "PF", amount: 1800,
                       reason: "Provident fund" },
    });
    // A finalised month refuses new deductions — that is the rule — so the
    // check below is made on a draft month instead.
    await api("POST", "/admin/payroll/generate", { token, body: { month: "2026-05" } });
    await api("POST", "/admin/payroll/2026-05/deductions", {
        token, body: { employee_id: "E001", kind: "PF", amount: 1800,
                       reason: "Provident fund" },
    });
    const draft = await lineFor("2026-05");
    slip = await api("GET", "/payroll/payslip/2026-05?employee_id=E001",
        { token, raw: true });

    check("the payslip names the provident fund that came off it",
        slip.text.includes("Provident fund"), "PF is not on the sheet");
    if (Number(draft.late_deduction) > 0) {
        check("and the lateness charge",
            slip.text.toLowerCase().includes("late"), "lateness is not on the sheet");
    }

    // EVERY DEDUCTION PRINTED, TOTALLED, AGAINST THE FROZEN FIGURE. This is
    // what "the net is lower and nothing says why" looks like as an assertion.
    const printed = [...slip.text.matchAll(/−₹([\d,]+\.\d{2})/g)]
        .map((m) => Number(m[1].replace(/,/g, "")));
    const printedTotal = money(printed.reduce((a, b) => a + b, 0));
    check("what the payslip prints as deductions adds up to what it took off",
        printedTotal === money(draft.total_deductions),
        `printed ${printedTotal}, frozen ${draft.total_deductions}`);

    // ── WHO A RUN DOES NOT COVER ────────────────────────────────────────
    //
    // A run's lines are frozen when it is generated, so somebody hired
    // afterwards is simply not on it — correct, and completely invisible:
    // you add an employee, open payroll, count one row short, and the page
    // has nothing to say. Reported as "employee add ho to yahan dikhe".
    //
    // The run does not gain a row (they were not paid by it); the RESPONSE
    // names them, so the page can say why the count is what it is.
    let run = await api("GET", "/admin/payroll/2026-05", { token });
    const before = (run.body.lines || []).length;
    check("nobody is missing from a freshly generated draft",
        (run.body.missing_employees || []).length === 0,
        JSON.stringify(run.body.missing_employees));

    await api("POST", "/admin/employees", {
        token,
        body: { employee_id: "E900", username: "adi", password: "GoodPass123",
                role: "employee", full_name: "adi" },
    });

    run = await api("GET", "/admin/payroll/2026-05", { token });
    check("a new hire is named as missing from the existing draft",
        (run.body.missing_employees || []).some((m) => m.employee_id === "E900"),
        JSON.stringify(run.body.missing_employees));
    check("and is NOT quietly added to it as a line",
        (run.body.lines || []).length === before,
        `${(run.body.lines || []).length} lines, was ${before}`);

    // GENERATING AGAIN IS WHAT PICKS THEM UP, which is what the page now
    // tells somebody to do.
    await api("POST", "/admin/payroll/generate", { token, body: { month: "2026-05" } });
    run = await api("GET", "/admin/payroll/2026-05", { token });
    check("regenerating the draft puts them on it",
        (run.body.lines || []).some((l) => l.employee_id === "E900"),
        JSON.stringify((run.body.lines || []).map((l) => l.employee_id)));
    check("and nobody is missing any more",
        (run.body.missing_employees || []).length === 0,
        JSON.stringify(run.body.missing_employees));

    // ── AND WHEN SOMEBODY IS DELETED ────────────────────────────────────
    await api("DELETE", "/admin/employees/E900", { token });
    await api("POST", "/admin/payroll/generate", { token, body: { month: "2026-05" } });
    run = await api("GET", "/admin/payroll/2026-05", { token });
    check("a deleted employee drops off the regenerated draft",
        !(run.body.lines || []).some((l) => l.employee_id === "E900"),
        JSON.stringify((run.body.lines || []).map((l) => l.employee_id)));
    check("and is not reported as missing from it either",
        !(run.body.missing_employees || []).some((m) => m.employee_id === "E900"),
        JSON.stringify(run.body.missing_employees));

    // THE FINALISED MONTH IS UNTOUCHED BY ALL OF THAT. It is the record of
    // what was paid; a hire, a deletion and two regenerations later it still
    // says exactly what it said.
    const sealed = await api("GET", "/admin/payroll/2026-06", { token });
    check("the finalised month still has its own lines",
        (sealed.body.lines || []).length > 0
        && sealed.body.run.status === "FINALIZED",
        `${(sealed.body.lines || []).length} lines, ${sealed.body.run?.status}`);

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
