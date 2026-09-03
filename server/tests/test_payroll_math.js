/**
 * The arithmetic of a payslip, checked against sums anybody can do by hand.
 *
 * This is the part of the product where a mistake means somebody is paid the
 * wrong amount. It touches no database and no request, so every case here is
 * a plain calculation with a plain answer — which is the point: an
 * accountant should be able to read this file and agree with it.
 *
 * Run:  node server/tests/test_payroll_math.js
 */
const { calculateLine, lateDeductionFor, splitCTC, statutoryFor,
        money } = require("../utils/payroll_math");

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}
const eq = (a, b) => Math.abs(Number(a) - Number(b)) < 0.005;

console.log("Payroll arithmetic\n");

console.log("A full month, nothing unusual");
let line = calculateLine({
    gross: 30000, workingDays: 26, presentDays: 26,
});
check("per day is the gross over the working days", eq(line.per_day, 1153.85),
    String(line.per_day));
check("nothing is deducted", eq(line.total_deductions, 0), String(line.total_deductions));
check("and the net is the gross", eq(line.net_pay, 30000), String(line.net_pay));

console.log("\nLeave that is paid changes nothing");
// The owner's rule: casual and sick are paid. Somebody who took four days of
// approved sick leave is paid the same as somebody who did not.
line = calculateLine({
    gross: 30000, workingDays: 26, presentDays: 22, paidLeaveDays: 4,
});
check("four days of casual or sick leave cost nothing",
    eq(line.net_pay, 30000), String(line.net_pay));
check("but they are still counted, so the payslip can show them",
    eq(line.paid_leave_days, 4), String(line.paid_leave_days));

console.log("\nUnpaid leave is deducted at the day rate");
line = calculateLine({
    gross: 26000, workingDays: 26, presentDays: 24, unpaidLeaveDays: 2,
});
check("the day rate is exact", eq(line.per_day, 1000), String(line.per_day));
check("two days come off", eq(line.unpaid_deduction, 2000), String(line.unpaid_deduction));
check("and the net is what is left", eq(line.net_pay, 24000), String(line.net_pay));

console.log("\nAbsence nobody approved is deducted the same way");
line = calculateLine({
    gross: 26000, workingDays: 26, presentDays: 23, absentDays: 3,
});
check("three days come off", eq(line.absent_deduction, 3000), String(line.absent_deduction));
check("net", eq(line.net_pay, 23000), String(line.net_pay));

console.log("\nHalf days are half");
line = calculateLine({
    gross: 26000, workingDays: 26, presentDays: 25.5, unpaidLeaveDays: 0.5,
});
check("half a day of unpaid leave costs half a day",
    eq(line.unpaid_deduction, 500), String(line.unpaid_deduction));
check("net", eq(line.net_pay, 25500), String(line.net_pay));

console.log("\nOvertime is hours times the rate somebody set");
line = calculateLine({
    gross: 30000, workingDays: 25, presentDays: 25,
    overtimeHours: 6.5, overtimeRate: 200,
});
check("six and a half hours at 200", eq(line.overtime_amount, 1300),
    String(line.overtime_amount));
check("added to the net", eq(line.net_pay, 31300), String(line.net_pay));

console.log("\nAdjustments, with their signs where they belong");
line = calculateLine({
    gross: 20000, workingDays: 20, presentDays: 20,
    adjustments: [
        { amount: 5000 },    // bonus
        { amount: -2000 },   // advance
        { amount: -500 },    // fine
    ],
});
check("they add up", eq(line.adjustments_total, 2500), String(line.adjustments_total));
check("and move the net", eq(line.net_pay, 22500), String(line.net_pay));
check("while the figure before them is kept, so the payslip can show both",
    eq(line.net_before_adjustments, 20000), String(line.net_before_adjustments));

console.log("\nEverything at once");
line = calculateLine({
    gross: 45000, workingDays: 24, presentDays: 18,
    paidLeaveDays: 2, unpaidLeaveDays: 3, absentDays: 1,
    overtimeHours: 4, overtimeRate: 350,
    adjustments: [{ amount: 1000 }, { amount: -1500 }],
});
// 45000 / 24 = 1875 a day.
//   unpaid 3 × 1875 = 5625
//   absent 1 × 1875 = 1875
//   overtime 4 × 350 = 1400
//   45000 − 5625 − 1875 + 1400 = 38900, then −500 of adjustments = 38400
check("per day", eq(line.per_day, 1875), String(line.per_day));
check("unpaid", eq(line.unpaid_deduction, 5625), String(line.unpaid_deduction));
check("absent", eq(line.absent_deduction, 1875), String(line.absent_deduction));
check("overtime", eq(line.overtime_amount, 1400), String(line.overtime_amount));
check("before adjustments", eq(line.net_before_adjustments, 38900),
    String(line.net_before_adjustments));
check("net pay", eq(line.net_pay, 38400), String(line.net_pay));

console.log("\nThe parts must add up to the whole");
// The thing anybody checking a payslip does first. Rounding each component
// separately and then adding them is how this stops being true.
for (const scenario of [
    { gross: 33333, workingDays: 23, unpaidLeaveDays: 1, absentDays: 2 },
    { gross: 47500, workingDays: 21, unpaidLeaveDays: 0.5, absentDays: 1.5 },
    { gross: 19999, workingDays: 26, unpaidLeaveDays: 3, overtimeHours: 7,
      overtimeRate: 137.5 },
    { gross: 100000, workingDays: 27, absentDays: 5.5 },
]) {
    const l = calculateLine(scenario);
    const sum = money(l.gross - l.unpaid_deduction - l.absent_deduction
                      + l.overtime_amount);
    check(`gross ${l.gross} over ${l.working_days} days adds up`,
        eq(sum, l.net_before_adjustments),
        `${sum} vs ${l.net_before_adjustments}`);
}

console.log("\nThe edges that would otherwise divide by zero");
line = calculateLine({ gross: 30000, workingDays: 0, absentDays: 0 });
check("a month with no working days does not divide by zero",
    Number.isFinite(line.per_day) && line.per_day === 0, String(line.per_day));
check("and pays the salary — nobody was expected to work",
    eq(line.net_pay, 30000), String(line.net_pay));

line = calculateLine({});
check("an empty call is zero, not NaN",
    Number.isFinite(line.net_pay) && line.net_pay === 0, String(line.net_pay));

line = calculateLine({ gross: 10000, workingDays: 20, absentDays: 20 });
check("a whole month absent pays nothing, and not less than nothing",
    eq(line.net_pay, 0), String(line.net_pay));


// ── what lateness costs, when a company decides it costs something ──────
//
// THE DEFAULT IS THAT IT COSTS NOTHING, and that is the case that matters
// most: every existing payslip was generated without a lateness policy, and
// adding one to the code must not have moved a single rupee on any of them.
console.log("\nLateness: a fact always, a charge only under a policy");

const late = (over) => lateDeductionFor({
    lateDays: 4, lateMinutes: 200, perDay: 1000, hoursPerDay: 8, ...over });

check("no policy, no charge", eq(late({}), 0), String(late({})));
check("an unrecognised policy charges nothing rather than guessing",
    eq(late({ mode: "SOMETHING_ELSE" }), 0), String(late({ mode: "SOMETHING_ELSE" })));

check("per-day: four late days at a thousand a day is four thousand",
    eq(late({ mode: "PER_DAY" }), 4000), String(late({ mode: "PER_DAY" })));
check("per-day: the excused days come off first",
    eq(late({ mode: "PER_DAY", freeDays: 2 }), 2000),
    String(late({ mode: "PER_DAY", freeDays: 2 })));
check("per-day: excusing more days than there were is not a credit",
    eq(late({ mode: "PER_DAY", freeDays: 9 }), 0),
    String(late({ mode: "PER_DAY", freeDays: 9 })));

// 200 minutes = 3h20m; a 1000-rupee day over 8 hours is 125 an hour.
check("pro-rata: the minutes at the hourly rate",
    eq(late({ mode: "PRO_RATA" }), 416.67), String(late({ mode: "PRO_RATA" })));
check("pro-rata: a zero-hour day does not divide by zero",
    eq(late({ mode: "PRO_RATA", hoursPerDay: 0 }), 0),
    String(late({ mode: "PRO_RATA", hoursPerDay: 0 })));
check("negative minutes cannot pay somebody for being late",
    eq(late({ mode: "PRO_RATA", lateMinutes: -600 }), 0),
    String(late({ mode: "PRO_RATA", lateMinutes: -600 })));

// And through a whole line, because that is where it reaches the net.
line = calculateLine({
    gross: 26000, workingDays: 26, presentDays: 26,
    lateDays: 3, lateMinutes: 90,
});
check("a line with lateness and no policy is paid in full",
    eq(line.net_pay, 26000), String(line.net_pay));
check("but the minutes are on the payslip either way",
    line.late_minutes === 90 && line.late_days === 3,
    `${line.late_days} days, ${line.late_minutes} minutes`);

line = calculateLine({
    gross: 26000, workingDays: 26, presentDays: 26,
    lateDays: 3, lateMinutes: 90,
    latePolicy: { mode: "PER_DAY", freeDays: 1, hoursPerDay: 8 },
});
// 26000/26 = 1000 a day; three late days less one excused = two.
check("with a per-day policy, two chargeable days cost two days' pay",
    eq(line.late_deduction, 2000) && eq(line.net_pay, 24000),
    `${line.late_deduction} / ${line.net_pay}`);
check("and lateness is counted in the total deductions",
    eq(line.total_deductions, 2000), String(line.total_deductions));

// ── deductions somebody entered ─────────────────────────────────────────
line = calculateLine({
    gross: 26000, workingDays: 26, presentDays: 26, otherDeductions: 1800,
});
check("an entered deduction comes off the net",
    eq(line.net_pay, 24200), String(line.net_pay));
check("a negative entered deduction cannot add to somebody's pay",
    eq(calculateLine({ gross: 26000, workingDays: 26, presentDays: 26,
                       otherDeductions: -5000 }).net_pay, 26000),
    String(calculateLine({ gross: 26000, workingDays: 26, presentDays: 26,
                           otherDeductions: -5000 }).net_pay));


// ── a cost to company, split into the parts a payslip prints ────────────
//
// THE ONE PROPERTY THAT MATTERS: the parts add back to the whole. A split
// that comes to 23,332.99 against a CTC of 23,333 is a payslip arguing with
// itself, and somebody has to be able to say where the rupee went.
console.log("\nSplitting a CTC");

const TEMPLATE = [
    { name: "Basic", rule: "PERCENT_CTC", value: 50 },
    { name: "DA", rule: "PERCENT_BASIC", value: 20 },
    { name: "House Rent Allowance", rule: "PERCENT_BASIC", value: 50 },
    { name: "Conveyance Allowance", rule: "PERCENT_BASIC", value: 15 },
    { name: "Fixed Allowance", rule: "BALANCE", value: 0 },
];

const totalOf = (rows) => money(rows.reduce((sum, r) => sum + r.monthly, 0));
const byName = (rows, name) => rows.find((r) => r.name === name).monthly;

let split = splitCTC(23333, TEMPLATE);
check("Basic is half the CTC", eq(byName(split, "Basic"), 11666.5),
    String(byName(split, "Basic")));
check("DA is a fifth of Basic", eq(byName(split, "DA"), 2333.3),
    String(byName(split, "DA")));
check("house rent is half of Basic",
    eq(byName(split, "House Rent Allowance"), 5833.25),
    String(byName(split, "House Rent Allowance")));
check("the parts add back to the CTC exactly",
    eq(totalOf(split), 23333), String(totalOf(split)));

// The awkward ones: a CTC that does not divide cleanly must still balance.
for (const ctc of [23333, 10000, 33333.33, 1, 87654.21, 1000000]) {
    check(`${ctc} splits without losing a paisa`,
        eq(totalOf(splitCTC(ctc, TEMPLATE)), money(ctc)),
        `${totalOf(splitCTC(ctc, TEMPLATE))} vs ${money(ctc)}`);
}

check("a CTC of zero is all zeroes, not a crash",
    eq(totalOf(splitCTC(0, TEMPLATE)), 0), String(totalOf(splitCTC(0, TEMPLATE))));
check("an empty template splits into nothing",
    splitCTC(50000, []).length === 0, String(splitCTC(50000, []).length));

// A template whose fixed parts already exceed the CTC is a template somebody
// has to fix. Paying a NEGATIVE allowance to make the sum work would hide it.
split = splitCTC(10000, [
    { name: "Basic", rule: "PERCENT_CTC", value: 50 },
    { name: "Something", rule: "FIXED", value: 90000 },
    { name: "Fixed Allowance", rule: "BALANCE", value: 0 },
]);
check("an over-committed template never pays a negative allowance",
    byName(split, "Fixed Allowance") === 0,
    String(byName(split, "Fixed Allowance")));

// ── statutory, and only when somebody switched it on ────────────────────
console.log("\nStatutory deductions apply only when configured");

const RATES = { epfRate: 12, epfCeiling: 15000, esiRate: 0.75,
                esiCeiling: 21000, professionalTax: 200 };

let due = statutoryFor({ basic: 11666.5, da: 2333.3, gross: 23333, rates: RATES,
                         enabled: {} });
check("nothing is deducted by default", eq(due.total, 0), String(due.total));

due = statutoryFor({ basic: 11666.5, da: 2333.3, gross: 23333, rates: RATES,
                     enabled: { epf: true } });
// Basic + DA is 13,999.80, under the 15,000 ceiling, so 12% of all of it.
check("provident fund is 12% of Basic plus DA", eq(due.epf, 1679.98),
    String(due.epf));

due = statutoryFor({ basic: 40000, da: 0, gross: 60000, rates: RATES,
                     enabled: { epf: true } });
// Above the ceiling the contribution stops rising: 12% of 15,000.
check("and it stops rising at the wage ceiling", eq(due.epf, 1800),
    String(due.epf));

due = statutoryFor({ basic: 8000, da: 0, gross: 18000, rates: RATES,
                     enabled: { esi: true } });
check("ESI is a share of gross under its ceiling", eq(due.esi, 135),
    String(due.esi));

due = statutoryFor({ basic: 20000, da: 0, gross: 40000, rates: RATES,
                     enabled: { esi: true } });
// ESI STOPS ENTIRELY above the ceiling — somebody over the limit is outside
// the scheme, not paying the maximum.
check("and stops entirely above it, rather than capping", eq(due.esi, 0),
    String(due.esi));

due = statutoryFor({ gross: 40000, rates: RATES, enabled: { pt: true } });
check("professional tax is the flat figure somebody set",
    eq(due.professional_tax, 200), String(due.professional_tax));


console.log("\nMoney never keeps more than paise");
line = calculateLine({ gross: 33333.33, workingDays: 23, unpaidLeaveDays: 1 });
for (const [name, value] of Object.entries(line)) {
    if (typeof value !== "number" || name.endsWith("_days")
        || name === "overtime_hours" || name === "overtime_rate") continue;
    check(`${name} is rounded to paise`,
        Math.abs(value * 100 - Math.round(value * 100)) < 1e-6, String(value));
}

console.log();
if (failures) {
    console.log(`${failures} failure(s)`);
    process.exit(1);
}
console.log("all payroll arithmetic checks passed");
