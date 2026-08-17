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
const { calculateLine, money } = require("../utils/payroll_math");

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
