/**
 * A salary set from a CTC, end to end.
 *
 * WHAT THIS IS REALLY GUARDING. Salary used to be one number. It is now a CTC
 * split into parts, and the whole of payroll — every run, every payslip, every
 * line already frozen in payroll_lines — still reads gross_monthly. The bridge
 * that makes that safe is one rule:
 *
 *     gross_monthly == the sum of the components
 *
 * If that ever stops holding, a payslip's parts stop adding up to the pay, and
 * nothing else in the system would notice: the run would go on using the gross
 * and the payslip would go on printing components that disagree with it.
 *
 * The other half is that NOTHING CHANGED FOR SALARIES SET THE OLD WAY. An
 * employee on a plain monthly gross has no components, no CTC, and must behave
 * exactly as before — including through a payroll run.
 *
 * Run:  node server/tests/test_salary_ctc.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_ctc_${process.pid}`;
const PORT = 8000 + ((process.pid + 857) % 1000);
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
    console.log("\nA salary set from a cost to company\n");

    migrate(DB);

    const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
    const seeded = await bcrypt.hash("SuperSecret123", 10);
    psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name, created_at)
              VALUES ('SA001','superadmin','${seeded}','super_admin','Owner','2026-01-01'),
                     ('E001','asha','${seeded}','employee','Asha Verma','2026-01-01'),
                     ('E002','bilal','${seeded}','employee','Bilal Khan','2026-01-01')`);

    psql(DB, `UPDATE employee_configs SET shift_start='09:00', shift_end='18:00',
                 weekly_offs='7', late_grace_minutes=10 WHERE employee_id IS NULL`);

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

    // ── a CTC becomes a structure ───────────────────────────────────────
    // 2,80,000 a year is 23,333.33 a month — the figure from the reference.
    let res = await api("POST", "/admin/payroll/salaries", {
        token,
        body: { employee_id: "E001", ctc_annual: 280000, overtime_hourly: 300,
                effective_from: "2026-01-01", remarks: "On a CTC" },
    });
    check("a salary can be set from an annual CTC", res.status === 200,
        JSON.stringify(res.body));

    const history = await api("GET", "/admin/payroll/salaries/E001", { token });
    const salary = (history.body.data || [])[0];
    const parts = salary?.components || [];
    check("and it comes back with its components", parts.length === 5,
        `${parts.length} components`);

    const named = (name) => Number(parts.find((c) => c.name === name)?.monthly);
    check("Basic is half the monthly CTC", named("Basic") === 11666.67,
        String(named("Basic")));
    check("DA is a fifth of Basic", named("DA") === 2333.33, String(named("DA")));
    // 5,833.33, not 5,833.34. Half of Basic is 5,833.3335 and rounds down;
    // the third of a paisa it leaves behind lands in the balancing allowance,
    // which is exactly what that component is for. The sum below is the check
    // that matters, and it is exact.
    check("house rent is half of Basic",
        named("House Rent Allowance") === 5833.33,
        String(named("House Rent Allowance")));
    check("conveyance is 15% of Basic",
        named("Conveyance Allowance") === 1750, String(named("Conveyance Allowance")));

    // THE RULE THE REST OF PAYROLL DEPENDS ON.
    const sum = parts.reduce((total, c) => total + Number(c.monthly), 0);
    check("the monthly gross IS the sum of the components",
        Math.abs(sum - Number(salary.gross_monthly)) < 0.005,
        `components ${sum.toFixed(2)} vs gross ${salary.gross_monthly}`);
    check("and the sum is the monthly CTC",
        Math.abs(sum - 280000 / 12) < 0.005, sum.toFixed(2));
    check("the CTC itself is kept", Number(salary.ctc_annual) === 280000,
        String(salary.ctc_annual));

    // ── statutory components are OFF unless switched on ─────────────────
    check("provident fund is off unless somebody enables it",
        salary.epf_enabled === false, String(salary.epf_enabled));

    res = await api("POST", "/admin/payroll/salaries", {
        token,
        body: { employee_id: "E001", ctc_annual: 280000, overtime_hourly: 300,
                effective_from: "2026-02-01", epf_enabled: true, pt_enabled: true },
    });
    check("and can be switched on for one person", res.status === 200,
        JSON.stringify(res.body.message));
    const withEpf = (await api("GET", "/admin/payroll/salaries/E001", { token }))
        .body.data[0];
    check("which is remembered against that version",
        withEpf.epf_enabled === true && withEpf.pt_enabled === true
        && withEpf.esi_enabled === false,
        `epf=${withEpf.epf_enabled} pt=${withEpf.pt_enabled} esi=${withEpf.esi_enabled}`);

    // ── the old way still works, untouched ──────────────────────────────
    res = await api("POST", "/admin/payroll/salaries", {
        token,
        body: { employee_id: "E002", gross_monthly: 38000, overtime_hourly: 250,
                effective_from: "2026-01-01" },
    });
    check("a plain monthly gross still saves, with no CTC", res.status === 200,
        JSON.stringify(res.body));
    const plain = (await api("GET", "/admin/payroll/salaries/E002", { token }))
        .body.data[0];
    check("and has no components invented for it",
        (plain.components || []).length === 0,
        JSON.stringify(plain.components));
    check("nor a CTC guessed by multiplying by twelve",
        plain.ctc_annual === null, String(plain.ctc_annual));

    // ── and a payroll run reads it exactly as before ────────────────────
    psql(DB, `INSERT INTO attendance (employee_id, login_time, logout_time)
              VALUES ('E001','2026-03-02 03:30:00','2026-03-02 12:30:00'),
                     ('E002','2026-03-02 03:30:00','2026-03-02 12:30:00')`);
    res = await api("POST", "/admin/payroll/generate",
        { token, body: { month: "2026-03" } });
    check("a month generates over a CTC-based salary", res.status === 200,
        JSON.stringify(res.body));

    const run = await api("GET", "/admin/payroll/2026-03", { token });
    const line = (run.body.lines || []).find((l) => l.employee_id === "E001");
    check("and the payslip's gross is the sum of the components",
        Math.abs(Number(line.gross_monthly) - sum) < 0.005,
        `${line.gross_monthly} vs ${sum.toFixed(2)}`);

    const other = (run.body.lines || []).find((l) => l.employee_id === "E002");
    check("while the plainly-paid colleague is unchanged",
        Number(other.gross_monthly) === 38000, String(other.gross_monthly));

    // ── a rise rewrites the split, and leaves the old one alone ─────────
    await api("POST", "/admin/payroll/salaries", {
        token,
        body: { employee_id: "E001", ctc_annual: 360000, overtime_hourly: 300,
                effective_from: "2026-04-01", remarks: "Promoted" },
    });
    const versions = (await api("GET", "/admin/payroll/salaries/E001", { token }))
        .body.data;
    check("a rise adds a version rather than editing the last",
        versions.length === 3, `${versions.length} versions`);
    const older = versions.find((v) => v.effective_from === "2026-01-01");
    check("and the older split is still the older split",
        Number(older.components.find((c) => c.name === "Basic").monthly) === 11666.67,
        JSON.stringify(older.components.find((c) => c.name === "Basic")));

    // ── statutory, applied automatically, and only once ─────────────────
    //
    // THE DANGEROUS FAILURE IS SILENT DOUBLING. Generating a month again is
    // routine — an attendance row is corrected, a late leave request is
    // approved — and the computed deductions have to be rebuilt with the
    // lines. If they were appended instead, every regeneration would add
    // another provident fund row on top of the last, and somebody would be
    // deducted twice with nothing failing anywhere.
    console.log("\n  Statutory deductions on a run");

    // Asha is enrolled in provident fund and professional tax from February.
    // March's run picks that up; Bilal, enrolled in nothing, gets neither.
    res = await api("POST", "/admin/payroll/generate",
        { token, body: { month: "2026-03" } });
    check("the month regenerates with statutory switched on", res.status === 200,
        JSON.stringify(res.body));

    const deductionsFor = async (id) => {
        const month = await api("GET", "/admin/payroll/2026-03", { token });
        const row = (month.body.lines || []).find((l) => l.employee_id === id);
        return { line: row, list: row?.deductions || [] };
    };

    let asha = await deductionsFor("E001");
    const pf = asha.list.filter((d) => d.kind === "PF");
    check("provident fund is deducted without anybody typing it",
        pf.length === 1, `${pf.length} PF rows`);
    // Basic 11,666.67 + DA 2,333.33 = 14,000, under the 15,000 ceiling,
    // so 12% of all of it.
    check("and it is 12% of Basic plus DA",
        Math.abs(Number(pf[0]?.amount) - 1680) < 0.02, String(pf[0]?.amount));
    check("professional tax comes with it",
        asha.list.filter((d) => d.kind === "PROFESSIONAL_TAX").length === 1,
        JSON.stringify(asha.list.map((d) => d.kind)));
    check("but not ESI, which was never switched on",
        asha.list.filter((d) => d.kind === "ESI").length === 0,
        JSON.stringify(asha.list.map((d) => d.kind)));

    const bilal = await deductionsFor("E002");
    check("and somebody enrolled in nothing is deducted nothing",
        bilal.list.length === 0, JSON.stringify(bilal.list));

    // A deduction somebody typed, which must survive every rebuild.
    await api("POST", "/admin/payroll/2026-03/deductions", {
        token,
        body: { employee_id: "E001", kind: "MANUAL", amount: 500,
                reason: "Canteen" },
    });

    // ── THE TEST THIS WHOLE COLUMN EXISTS FOR ───────────────────────────
    for (let round = 0; round < 3; round += 1) {
        await api("POST", "/admin/payroll/generate",
            { token, body: { month: "2026-03" } });
    }
    asha = await deductionsFor("E001");
    check("three more regenerations leave exactly one provident fund row",
        asha.list.filter((d) => d.kind === "PF").length === 1,
        `${asha.list.filter((d) => d.kind === "PF").length} PF rows`);
    check("and exactly one professional tax row",
        asha.list.filter((d) => d.kind === "PROFESSIONAL_TAX").length === 1,
        `${asha.list.filter((d) => d.kind === "PROFESSIONAL_TAX").length} rows`);
    check("the hand-entered deduction survives every rebuild",
        asha.list.filter((d) => d.kind === "MANUAL").length === 1,
        JSON.stringify(asha.list.map((d) => `${d.kind} ${d.amount}`)));

    // AND THE LINE AGREES WITH THE ROWS BESIDE IT. 1,680 + 200 + 500 = 2,380.
    const itemised = asha.list.reduce((sum, d) => sum + Number(d.amount), 0);
    check("the line's entered-deduction total is the sum of its rows",
        Math.abs(Number(asha.line.other_deductions) - itemised) < 0.02,
        `line ${asha.line.other_deductions} vs rows ${itemised.toFixed(2)}`);

    // ── and the Benefits card reads the same money ──────────────────────
    //
    // "previous", not "this". The financial year runs April to March, so a
    // March 2026 payroll belongs to 2025-26 — the year BEFORE the one running
    // now. Asking for "this year" and expecting March in it is the mistake
    // this comment exists to stop somebody making again.
    const benefits = await api("GET", "/admin/payroll/benefits?span=previous",
        { token });
    check("the benefits card sums the provident fund actually deducted",
        Math.abs(Number(benefits.body.epf?.total) - Number(pf[0].amount)) < 0.02,
        JSON.stringify(benefits.body.epf));
    check("and reports ESI as not deducted rather than as zero",
        benefits.body.esi?.configured === false,
        JSON.stringify(benefits.body.esi));

    // The current financial year has no payroll in it at all, and must say so
    // rather than reporting the other year's money.
    const thisYear = await api("GET", "/admin/payroll/benefits?span=this",
        { token });
    check("and a year with no payroll in it reports nothing, not last year's",
        thisYear.body.epf?.configured === false,
        JSON.stringify(thisYear.body.epf));

    // ── A COMPONENT SET BY HAND ─────────────────────────────────────────
    //
    // "Dono option rakho — auto bhi, aur zaroorat pade to haath se, kyunki
    // bahut saare components variable hote hain." The CTC still fills every
    // row; any row but the balance may be typed over, and the balance takes
    // up the difference so the parts still equal the CTC.
    //
    // A figure typed by hand travels as the template's own vocabulary —
    // FIXED, with the amount as its value — so the split rules are unchanged.
    console.log("\nA component set by hand");
    const TEMPLATE = [
        { name: "Basic", rule: "PERCENT_CTC", value: 50 },
        { name: "DA", rule: "PERCENT_BASIC", value: 20 },
        { name: "House Rent Allowance", rule: "PERCENT_BASIC", value: 50 },
        { name: "Conveyance Allowance", rule: "PERCENT_BASIC", value: 15 },
        { name: "Fixed Allowance", rule: "BALANCE", value: 0 },
    ];
    const withHra = TEMPLATE.map((c) => (c.name === "House Rent Allowance"
        ? { ...c, rule: "FIXED", value: 15000 } : c));
    res = await api("POST", "/admin/payroll/salaries", {
        token, body: { employee_id: "E002", ctc_annual: 600000, overtime_hourly: 0,
                       effective_from: "2026-03-01", components: withHra } });
    check("a salary with one component typed over is accepted",
        res.status === 200 && res.body.success, JSON.stringify(res.body).slice(0, 160));

    const part = (list, name) => (list || []).find((c) => c.name === name);
    const saved = res.body.salary?.components;
    check("the typed figure is kept exactly", part(saved, "House Rent Allowance")?.monthly === 15000,
        JSON.stringify(part(saved, "House Rent Allowance")));
    check("and the balance takes up the difference, so the parts equal the CTC",
        Math.abs(saved.reduce((t, c) => t + c.monthly, 0) - 50000) < 0.01,
        `parts come to ${saved.reduce((t, c) => t + c.monthly, 0)}`);

    // THE OVERRIDE COMES BACK. The page rebuilt every split from the company
    // template when a person was opened, so an override was saved, shown as
    // gone the next time, and erased by the next save.
    const listed = (await api("GET", "/admin/payroll/salaries", { token }))
        .body.data.find((r) => r.employee_id === "E002");
    check("the salaries list carries each person's own split",
        Array.isArray(listed?.components) && listed.components.length === 5,
        JSON.stringify(listed?.components));
    check("with the hand-set component still marked as set by hand",
        part(listed.components, "House Rent Allowance")?.rule === "FIXED",
        JSON.stringify(part(listed.components, "House Rent Allowance")));

    // BASIC SET BY HAND. The allowances are shares of Basic; found as "the
    // percentage of the CTC" there was no Basic once it was FIXED, and DA,
    // HRA and conveyance all came out at zero. Measured before the fix.
    const withBasic = TEMPLATE.map((c) => (c.name === "Basic"
        ? { ...c, rule: "FIXED", value: 20000 } : c));
    res = await api("POST", "/admin/payroll/salaries", {
        token, body: { employee_id: "E002", ctc_annual: 600000, overtime_hourly: 0,
                       effective_from: "2026-03-01", components: withBasic } });
    const basicSplit = res.body.salary?.components || [];
    check("setting Basic by hand keeps the allowances as shares of it",
        part(basicSplit, "DA")?.monthly === 4000
        && part(basicSplit, "House Rent Allowance")?.monthly === 10000
        && part(basicSplit, "Conveyance Allowance")?.monthly === 3000,
        basicSplit.map((c) => `${c.name}=${c.monthly}`).join(", "));

    // AND A SPLIT THAT OVERSHOOTS IS REFUSED. The balance cannot go below
    // zero, so an overshoot would otherwise be saved as a gross larger than
    // the CTC — a raise nobody meant to give.
    const before = psql(DB, `SELECT gross_monthly FROM employee_salaries
                              WHERE employee_id='E002' AND effective_from='2026-03-01'`);
    const tooBig = TEMPLATE.map((c) => (c.name === "Basic"
        ? { ...c, rule: "FIXED", value: 40000 } : c));
    res = await api("POST", "/admin/payroll/salaries", {
        token, body: { employee_id: "E002", ctc_annual: 600000, overtime_hourly: 0,
                       effective_from: "2026-03-01", components: tooBig } });
    check("figures that come to more than the CTC are refused",
        res.status === 400, `${res.status} ${res.body.message || ""}`);
    check("and the salary saved before is left as it was",
        psql(DB, `SELECT gross_monthly FROM employee_salaries
                   WHERE employee_id='E002' AND effective_from='2026-03-01'`) === before,
        `was ${before}`);

    // A save with no components still uses the company's template, exactly
    // as before this existed.
    res = await api("POST", "/admin/payroll/salaries", {
        token, body: { employee_id: "E002", ctc_annual: 600000, overtime_hourly: 0,
                       effective_from: "2026-04-01" } });
    check("a save with no split of its own still uses the company's",
        part(res.body.salary?.components, "Basic")?.rule === "PERCENT_CTC"
        && part(res.body.salary?.components, "Basic")?.monthly === 25000,
        JSON.stringify(part(res.body.salary?.components, "Basic")));

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
