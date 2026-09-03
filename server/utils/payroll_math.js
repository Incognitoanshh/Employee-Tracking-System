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
 *   Lateness       recorded always, deducted only under a policy (below)
 *   Overtime       hours × the rate an administrator set
 *   Net            gross − deductions + overtime + adjustments
 *
 * LATENESS IS A FACT; CHARGING FOR IT IS A POLICY. Being 340 minutes late over
 * a month belongs on the payslip either way — a company that does not deduct
 * for it still wants to see it. So the minutes are always carried, and
 * lateDeduction is only ever what a configured policy asked for. With no
 * policy configured the figure is zero and the net is exactly what it was
 * before lateness was recorded at all.
 *
 * WHAT IT DOES NOT DO, deliberately: no PF, no ESI, no TDS, no professional
 * tax. Those are statutory, differ by company and by year, and deducting them
 * wrongly is a legal problem rather than a bug. They are not guessed at here —
 * they are carried as amounts somebody who knows the company's obligations
 * entered, which is what `otherDeductions` is.
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
 * What a lateness policy costs, if there is one.
 *
 * SEPARATE AND EXPORTED so the one place that decides whether being late costs
 * money can be tested on its own, and so a company that changes its mind
 * changes a setting rather than a formula buried in a payroll run.
 *
 * @param {object} input
 * @param {string} input.mode        NONE | PER_DAY | PRO_RATA
 * @param {number} input.lateDays    days the person arrived late
 * @param {number} input.lateMinutes total minutes late across those days
 * @param {number} input.freeDays    late days excused before charging begins
 * @param {number} input.perDay      one day's pay
 * @param {number} input.hoursPerDay the working day, for the hourly rate
 */
function lateDeductionFor({
    mode = "NONE",
    lateDays = 0,
    lateMinutes = 0,
    freeDays = 0,
    perDay = 0,
    hoursPerDay = 8,
} = {}) {
    if (mode === "PER_DAY") {
        // The first `freeDays` late arrivals are excused; each one after that
        // costs a day's pay. Half days do not arise here — somebody is late
        // on a day or they are not.
        const chargeable = Math.max(0, days(lateDays) - days(freeDays));
        return money(chargeable * Number(perDay || 0));
    }
    if (mode === "PRO_RATA") {
        // The minutes themselves, at the hourly rate. `freeDays` is NOT
        // applied here, and that is a decision rather than an oversight:
        // excusing "the first two late days" would mean knowing which days
        // those minutes belonged to, and the total does not carry that. A
        // company wanting both should charge per day.
        const hours = Math.max(0, Number(lateMinutes || 0)) / 60;
        const perHour = Number(hoursPerDay) > 0
            ? Number(perDay || 0) / Number(hoursPerDay) : 0;
        return money(hours * perHour);
    }
    return 0;                       // NONE, and anything unrecognised
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
 * @param {number} input.lateDays         days the person arrived late
 * @param {number} input.lateMinutes      total minutes late
 * @param {object} input.latePolicy       see lateDeductionFor; omitted = none
 * @param {number} input.otherDeductions  PF, ESI, PT, manual — entered, never
 *                                        computed. Positive; it is taken away.
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
    lateDays = 0,
    lateMinutes = 0,
    latePolicy = null,
    otherDeductions = 0,
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

    // Charged against the EXACT per-day rate, like the deductions above, for
    // the same reason: a rate rounded first and multiplied after carries its
    // rounding error into every day it touches.
    const lateCharge = latePolicy
        ? lateDeductionFor({ ...latePolicy, lateDays, lateMinutes,
                             perDay: exactPerDay })
        : 0;

    // Entered by hand — PF, ESI, professional tax, a manual line. Never
    // negative: a deduction that adds to somebody's pay is an adjustment, and
    // the two are kept apart so a year-end total of "what we deducted" means
    // what it says.
    const entered = Math.max(0, money(otherDeductions));

    const overtimeAmount = money(Number(overtimeHours || 0) * Number(overtimeRate || 0));

    const netBeforeAdjustments = money(
        grossAmount - unpaidDeduction - absentDeduction - lateCharge - entered
        + overtimeAmount);

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
        late_days: days(lateDays),
        late_minutes: Math.max(0, Math.round(Number(lateMinutes) || 0)),
        late_deduction: lateCharge,
        other_deductions: entered,
        total_deductions: money(
            unpaidDeduction + absentDeduction + lateCharge + entered),
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


/**
 * Split a monthly cost to company into the parts a payslip prints.
 *
 * THE PARTS ALWAYS ADD BACK TO THE WHOLE. That is the one property this has
 * to have: a split whose components sum to 23,332.99 against a CTC of 23,333
 * is a payslip that argues with itself, and the rupee has to be somewhere.
 * The BALANCE component absorbs it — it is defined as "whatever is left",
 * which is exactly what a fixed allowance is for.
 *
 * Rules, in the order they can depend on each other:
 *   PERCENT_CTC     value% of the monthly CTC
 *   PERCENT_BASIC   value% of whatever Basic came to
 *   FIXED           value rupees, as entered
 *   BALANCE         the CTC less everything above it
 *
 * Basic is computed first because the others are shares of it. A template
 * with no Basic is not an error — the percentages of it are simply zero.
 *
 * @param {number} ctcMonthly
 * @param {Array<{name:string, rule:string, value:number}>} template
 * @returns {Array<{name, rule, value, monthly}>}
 */
function splitCTC(ctcMonthly, template = []) {
    const ctc = money(ctcMonthly);
    const rows = (template || []).filter((row) => row && row.name);

    const basicRow = rows.find((row) => row.rule === "PERCENT_CTC");
    // UNROUNDED, and deliberately. Every allowance is a share of Basic, so
    // rounding Basic first and taking 50% of the rounded figure spreads that
    // rounding into all of them.
    const exactBasic = basicRow
        ? ctc * (Number(basicRow.value) || 0) / 100
        : 0;

    const out = rows.map((row) => {
        const value = Number(row.value) || 0;
        let monthly = 0;
        if (row.rule === "PERCENT_CTC") monthly = ctc * value / 100;
        else if (row.rule === "PERCENT_BASIC") monthly = exactBasic * value / 100;
        else if (row.rule === "FIXED") monthly = value;
        return { name: String(row.name), rule: String(row.rule), value,
                 monthly: row.rule === "BALANCE" ? 0 : money(monthly) };
    });

    // The balance, from the ROUNDED components — so what is left over is what
    // is actually left over once the printed figures are added up.
    const spent = out.reduce(
        (sum, row) => sum + (row.rule === "BALANCE" ? 0 : row.monthly), 0);
    const balance = out.find((row) => row.rule === "BALANCE");
    if (balance) {
        // Never negative: a template whose fixed parts exceed the CTC is a
        // template somebody has to fix, and paying a negative allowance to
        // paper over it would hide exactly that.
        balance.monthly = money(Math.max(0, ctc - spent));
    }
    return out;
}

/**
 * What the statutory deductions come to, for an employee they are ON for.
 *
 * NOTHING IS APPLIED UNLESS SOMEBODY SWITCHED IT ON. These are rates a
 * company is obliged to get right, they differ by state and by year, and a
 * wrong one is a legal problem rather than a bug. So this computes what a
 * CONFIGURED rate produces and never decides that a rate applies.
 *
 * @param {object} input
 * @param {number} input.basic       Basic
 * @param {number} input.da          dearness allowance
 * @param {number} input.gross       the whole monthly gross
 * @param {object} input.rates       epfRate, epfCeiling, esiRate, esiCeiling,
 *                                   professionalTax
 * @param {object} input.enabled     { epf, esi, pt }
 */
function statutoryFor({ basic = 0, da = 0, gross = 0, rates = {}, enabled = {} } = {}) {
    const out = { epf: 0, esi: 0, professional_tax: 0 };

    if (enabled.epf) {
        // Provident fund is a share of Basic + DA, and only up to the wage
        // ceiling — above it the contribution stops rising. A ceiling of 0
        // means "no ceiling", which is how some employers run it.
        const wages = money(Number(basic) + Number(da));
        const ceiling = Number(rates.epfCeiling) || 0;
        const base = ceiling > 0 ? Math.min(wages, ceiling) : wages;
        out.epf = money(base * (Number(rates.epfRate) || 0) / 100);
    }

    if (enabled.esi) {
        // ESI stops entirely above its ceiling rather than being capped at
        // it — somebody earning over the limit is outside the scheme, not
        // paying the maximum.
        const ceiling = Number(rates.esiCeiling) || 0;
        if (!(ceiling > 0) || Number(gross) <= ceiling) {
            out.esi = money(Number(gross) * (Number(rates.esiRate) || 0) / 100);
        }
    }

    if (enabled.pt) {
        out.professional_tax = money(Number(rates.professionalTax) || 0);
    }

    out.total = money(out.epf + out.esi + out.professional_tax);
    return out;
}

module.exports = {
    calculateLine, workingDaysOfMonth, lateDeductionFor, splitCTC,
    statutoryFor, money, days,
};
