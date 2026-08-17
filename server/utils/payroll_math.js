/**
 * The arithmetic of a payslip, with nothing else in it.
 *
 * SEPARATE FROM THE CONTROLLER ON PURPOSE. This is the part where a mistake
 * means somebody is paid the wrong amount, so it takes plain numbers, returns
 * plain numbers, touches no database and no request — and can therefore be
 * tested exhaustively against arithmetic anybody can check by hand.
 *
 * THE RULES, as the owner set them:
 *
 *   Working days   = days in the month − weekly offs − holidays
 *   Per day        = gross ÷ working days
 *   Casual leave   no deduction
 *   Sick leave     no deduction
 *   Unpaid leave   deducted at the per-day rate
 *   Absent         deducted at the per-day rate — a day nobody approved
 *   Overtime       hours × the rate an administrator set
 *   Net            gross − deductions + overtime + adjustments
 *
 * WHAT IT DOES NOT DO, deliberately: no PF, no ESI, no TDS. Those are
 * statutory, differ by company and by year, and deducting them wrongly is a
 * legal problem rather than a bug. They are not guessed at here.
 *
 * MONEY IS ROUNDED TO PAISE, ONCE, AT THE END OF EACH COMPONENT. Rounding
 * halfway through and again later is how a payslip's parts stop adding up to
 * its total — the thing anybody checking it notices first.
 */

/** Two decimal places, away from zero, so −0.005 does not become −0.00. */
function money(value) {
    const n = Number(value) || 0;
    return Math.sign(n) * Math.round(Math.abs(n) * 100) / 100;
}

/** One decimal place — days come in halves. */
function days(value) {
    return Math.round((Number(value) || 0) * 10) / 10;
}

/**
 * Work out one person's month.
 *
 * @param {object} input
 * @param {number} input.gross            monthly gross, before anything
 * @param {number} input.workingDays      the month's working days
 * @param {number} input.presentDays      days with attendance
 * @param {number} input.paidLeaveDays    casual + sick, approved
 * @param {number} input.unpaidLeaveDays  approved unpaid leave
 * @param {number} input.absentDays       working days with neither
 * @param {number} input.overtimeHours    entered by an administrator
 * @param {number} input.overtimeRate     per hour
 * @param {Array<{amount:number}>} input.adjustments
 */
function calculateLine({
    gross = 0,
    workingDays = 0,
    presentDays = 0,
    paidLeaveDays = 0,
    unpaidLeaveDays = 0,
    absentDays = 0,
    overtimeHours = 0,
    overtimeRate = 0,
    adjustments = [],
} = {}) {
    const grossAmount = money(gross);

    // A MONTH WITH NO WORKING DAYS PAYS THE FULL SALARY, and does not divide
    // by zero. It can only happen if every day is a holiday or a weekly off,
    // which means nobody was expected to work — so there is nothing to
    // deduct, and the per-day rate is meaningless rather than infinite.
    const working = days(workingDays);
    const perDay = working > 0 ? money(grossAmount / working) : 0;

    const unpaidDays = days(unpaidLeaveDays);
    const absent = days(absentDays);

    // Deductions are computed from the UNROUNDED per-day rate and rounded
    // once. Multiplying an already-rounded rate by twenty days multiplies the
    // rounding error with it — up to fifty paise adrift on a month, which is
    // exactly the kind of thing somebody notices on a payslip and nobody can
    // explain.
    const exactPerDay = working > 0 ? grossAmount / working : 0;
    const unpaidDeduction = money(exactPerDay * unpaidDays);
    const absentDeduction = money(exactPerDay * absent);

    const overtimeAmount = money(Number(overtimeHours || 0) * Number(overtimeRate || 0));

    const netBeforeAdjustments = money(
        grossAmount - unpaidDeduction - absentDeduction + overtimeAmount);

    const adjustmentTotal = money(
        (adjustments || []).reduce((sum, a) => sum + (Number(a.amount) || 0), 0));

    return {
        gross: grossAmount,
        working_days: working,
        present_days: days(presentDays),
        paid_leave_days: days(paidLeaveDays),
        unpaid_leave_days: unpaidDays,
        absent_days: absent,
        per_day: perDay,
        unpaid_deduction: unpaidDeduction,
        absent_deduction: absentDeduction,
        total_deductions: money(unpaidDeduction + absentDeduction),
        overtime_hours: Number(overtimeHours) || 0,
        overtime_rate: Number(overtimeRate) || 0,
        overtime_amount: overtimeAmount,
        net_before_adjustments: netBeforeAdjustments,
        adjustments_total: adjustmentTotal,
        net_pay: money(netBeforeAdjustments + adjustmentTotal),
    };
}

/**
 * Which days of a month were working days.
 *
 * The same weekly-off and holiday rules attendance and the reports use — this
 * calls into them rather than repeating them, so a month cannot have one
 * number of working days on the payslip and another in the report.
 */
function workingDaysOfMonth({ monthStart, monthEnd, weeklyOffs, holidays,
                              isNonWorkingDay }) {
    const list = [];
    const day = new Date(`${monthStart}T00:00:00Z`);
    const last = new Date(`${monthEnd}T00:00:00Z`);
    while (day <= last) {
        const iso = day.toISOString().slice(0, 10);
        if (!isNonWorkingDay(iso, weeklyOffs, holidays)) list.push(iso);
        day.setUTCDate(day.getUTCDate() + 1);
    }
    return list;
}

module.exports = { calculateLine, workingDaysOfMonth, money, days };
