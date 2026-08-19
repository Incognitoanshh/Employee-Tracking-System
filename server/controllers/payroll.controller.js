/**
 * Payroll: salaries, a month's run, and the payslip that comes out of it.
 *
 * THE ONE RULE EVERYTHING ELSE FOLLOWS FROM. A payslip is a statement about a
 * month that has already happened. Once the run is finalised its numbers are
 * frozen — a holiday added in September must not change August's pay, and
 * neither must a salary rise, a corrected shift, or leave approved late.
 *
 * So generating a run WRITES the figures down. Nothing here recomputes a
 * finalised line on read, and the only way to change a finalised month is to
 * add an adjustment, which is recorded with a reason and whoever made it.
 *
 * WHO MAY DO WHAT
 *   an admin      sets salaries, generates, finalises, adjusts.
 *   an employee   reads their OWN payslips and nothing else. There is no
 *                 employee route here that takes an employee id.
 *
 * WHAT IS NOT HERE: PF, ESI, TDS. They are statutory, differ by company and
 * by year, and deducting them wrongly is a legal problem rather than a bug.
 */
const pool = require("../config/db");
const { isNonWorkingDay } = require("../utils/attendance_status");
const { calculateLine, workingDaysOfMonth, money } = require("../utils/payroll_math");
const { istDate } = require("../utils/ist_sql");
const mailer = require("../utils/mailer");

const KINDS = ["BONUS", "INCENTIVE", "REIMBURSEMENT", "ADVANCE", "FINE", "OTHER"];
// Which way each one moves money, so a fine can never be typed as a credit.
const NEGATIVE_KINDS = new Set(["ADVANCE", "FINE"]);

const me = (req) => req.employee?.employee_id;
const isAdmin = (req) => ["admin", "super_admin"].includes(req.employee?.role);

const fail = (res, status, message) =>
    res.status(status).json({ success: false, message });

function serverError(res, req, error) {
    console.error("[500]", req.method, req.originalUrl, error.message);
    return res.status(500).json({ success: false, message: "Internal server error" });
}

/** "2026-08" → "2026-08-01". Anything else is not a month. */
function monthStart(value) {
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(String(value || ""))) return null;
    return `${value}-01`;
}

async function writeAudit(employeeId, activity) {
    await pool.query(
        `INSERT INTO activity_logs (employee_id, activity) VALUES ($1, $2)`,
        [employeeId, activity]).catch(() => {});
}

// ────────────────────────────────────────────────────────────── salaries

/** GET /api/admin/payroll/salaries — what everybody is on now. */
exports.listSalaries = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    try {
        const rows = await pool.query(
            `SELECT e.employee_id,
                    COALESCE(e.full_name, e.username) AS employee_name,
                    e.role, e.designation,
                    s.gross_monthly, s.overtime_hourly,
                    s.effective_from::text
               FROM employees e
               LEFT JOIN LATERAL (
                   -- The salary in force TODAY: the most recent one that has
                   -- already taken effect. A rise dated next month is stored
                   -- and does not show here until it applies.
                   SELECT gross_monthly, overtime_hourly, effective_from
                     FROM employee_salaries
                    WHERE employee_id = e.employee_id
                      AND effective_from <= (NOW() AT TIME ZONE 'Asia/Kolkata')::date
                    ORDER BY effective_from DESC LIMIT 1
               ) s ON TRUE
              WHERE e.role <> 'super_admin'
              ORDER BY e.employee_id`);
        return res.json({ success: true, data: rows.rows });
    } catch (error) {
        return serverError(res, req, error);
    }
};

/** POST /api/admin/payroll/salaries — set one, from a date. */
exports.setSalary = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const { employee_id, gross_monthly, overtime_hourly, effective_from } = req.body || {};

    if (!employee_id) return fail(res, 400, "Which employee?");
    const gross = Number(gross_monthly);
    if (!Number.isFinite(gross) || gross < 0) {
        return fail(res, 400, "The monthly gross must be a number, and not negative.");
    }
    if (gross > 100000000) {
        return fail(res, 400, "That is more than ten crore a month — check the figure.");
    }
    const overtime = Number(overtime_hourly ?? 0);
    if (!Number.isFinite(overtime) || overtime < 0) {
        return fail(res, 400, "The overtime rate must be a number, and not negative.");
    }
    const from = effective_from || null;
    if (from && !/^\d{4}-\d{2}-\d{2}$/.test(from)) {
        return fail(res, 400, "The date must be YYYY-MM-DD.");
    }

    try {
        const target = await pool.query(
            `SELECT role, COALESCE(full_name, username) AS name FROM employees
              WHERE employee_id = $1`, [employee_id]);
        if (target.rowCount === 0) return fail(res, 404, "No such employee.");
        if (target.rows[0].role === "super_admin") {
            return fail(res, 400, "The super admin is the owner, not an employee on payroll.");
        }

        const row = (await pool.query(
            `INSERT INTO employee_salaries
                 (employee_id, gross_monthly, overtime_hourly, effective_from, created_by)
             VALUES ($1, $2, $3,
                     COALESCE($4::date, DATE_TRUNC('month',
                              (NOW() AT TIME ZONE 'Asia/Kolkata'))::date), $5)
             ON CONFLICT (employee_id, effective_from)
             DO UPDATE SET gross_monthly = EXCLUDED.gross_monthly,
                           overtime_hourly = EXCLUDED.overtime_hourly,
                           created_by = EXCLUDED.created_by,
                           created_at = NOW() AT TIME ZONE 'UTC'
             RETURNING id, employee_id, gross_monthly, overtime_hourly,
                       effective_from::text`,
            [employee_id, gross, overtime, from, me(req)])).rows[0];

        await writeAudit(employee_id,
            `SALARY SET : ${gross} per month from ${row.effective_from} by ${me(req)}`);
        return res.json({ success: true, salary: row });
    } catch (error) {
        return serverError(res, req, error);
    }
};

/** GET /api/admin/payroll/salaries/:employee_id — the history of one person's pay. */
exports.salaryHistory = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    try {
        const rows = await pool.query(
            `SELECT s.id, s.gross_monthly, s.overtime_hourly,
                    s.effective_from::text, s.created_at,
                    COALESCE(c.full_name, c.username) AS created_by_name
               FROM employee_salaries s
               LEFT JOIN employees c ON c.employee_id = s.created_by
              WHERE s.employee_id = $1
              ORDER BY s.effective_from DESC`, [req.params.employee_id]);
        return res.json({ success: true, data: rows.rows });
    } catch (error) {
        return serverError(res, req, error);
    }
};

// ─────────────────────────────────────────────────────────── generating

/**
 * Everything a month's payroll needs, gathered once.
 *
 * The days come from the same rules attendance and the reports use, so a
 * month cannot have one number of working days on the payslip and another in
 * the report.
 */
async function gatherMonth(monthFirst) {
    const bounds = (await pool.query(
        `SELECT $1::date AS first,
                (DATE_TRUNC('month', $1::date) + INTERVAL '1 month - 1 day')::date AS last`,
        [monthFirst])).rows[0];
    const first = String(bounds.first).slice(0, 10);
    const last = String(bounds.last).slice(0, 10);

    const global = (await pool.query(
        `SELECT weekly_offs FROM employee_configs WHERE employee_id IS NULL`
    )).rows[0] || {};

    const perEmployee = new Map((await pool.query(
        `SELECT employee_id, weekly_offs FROM employee_configs
          WHERE employee_id IS NOT NULL`)).rows.map((r) => [r.employee_id, r.weekly_offs]));

    const holidays = new Set((await pool.query(
        `SELECT TO_CHAR(holiday_date,'YYYY-MM-DD') AS d FROM holidays
          WHERE holiday_date BETWEEN $1::date AND $2::date`, [first, last]))
        .rows.map((r) => r.d));

    // Attendance: which days each person actually turned up.
    const present = new Map();
    for (const row of (await pool.query(
        `SELECT employee_id, ${istDate("login_time")}::text AS d
           FROM attendance
          WHERE ${istDate("login_time")} BETWEEN $1::date AND $2::date
          GROUP BY employee_id, ${istDate("login_time")}`, [first, last])).rows) {
        if (!present.has(row.employee_id)) present.set(row.employee_id, new Set());
        present.get(row.employee_id).add(row.d);
    }

    // Approved leave, one entry per day, with what kind it was — the kind is
    // what decides whether it costs anything.
    const leave = new Map();
    for (const row of (await pool.query(
        `SELECT l.employee_id, TO_CHAR(day::date,'YYYY-MM-DD') AS d,
                l.leave_type, l.half_day
           FROM leave_requests l
           CROSS JOIN LATERAL generate_series(l.start_date, l.end_date, '1 day') AS day
          WHERE l.status = 'APPROVED'
            AND day::date BETWEEN $1::date AND $2::date`, [first, last])).rows) {
        if (!leave.has(row.employee_id)) leave.set(row.employee_id, new Map());
        leave.get(row.employee_id).set(row.d,
            { type: row.leave_type, cost: row.half_day ? 0.5 : 1 });
    }

    return { first, last, global, perEmployee, holidays, present, leave };
}

/**
 * POST /api/admin/payroll/generate — build (or rebuild) a month as a draft.
 *
 * Rebuilding a DRAFT is allowed and expected: somebody corrects an
 * attendance row or approves a late leave request and generates again. A
 * FINALIZED month is refused — that is what finalising means.
 */
exports.generate = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const first = monthStart(req.body?.month);
    if (!first) return fail(res, 400, "Month must be YYYY-MM, e.g. 2026-08.");

    try {
        const existing = await pool.query(
            `SELECT id, status FROM payroll_runs WHERE month = $1::date`, [first]);
        if (existing.rowCount && existing.rows[0].status === "FINALIZED") {
            return fail(res, 409,
                "That month is finalised. Add an adjustment instead of regenerating it.");
        }

        const month = await gatherMonth(first);
        const people = (await pool.query(
            `SELECT e.employee_id, COALESCE(e.full_name, e.username) AS name,
                    ${istDate("e.created_at")}::text AS joined_on,
                    s.gross_monthly, s.overtime_hourly
               FROM employees e
               LEFT JOIN LATERAL (
                   SELECT gross_monthly, overtime_hourly FROM employee_salaries
                    WHERE employee_id = e.employee_id
                      AND effective_from <= $1::date
                    ORDER BY effective_from DESC LIMIT 1
               ) s ON TRUE
              WHERE e.role <> 'super_admin'
              ORDER BY e.employee_id`, [month.last])).rows;

        const client = await pool.connect();
        let runId;
        try {
            await client.query("BEGIN");

            // The working days of the month, by the global calendar. Somebody
            // with their own weekly offs gets their own count below.
            const globalWorking = workingDaysOfMonth({
                monthStart: month.first, monthEnd: month.last,
                weeklyOffs: month.global.weekly_offs, holidays: month.holidays,
                isNonWorkingDay,
            }).length;

            runId = existing.rowCount ? existing.rows[0].id : null;
            if (runId) {
                await client.query(
                    `UPDATE payroll_runs SET working_days = $2, generated_by = $3,
                            generated_at = NOW() AT TIME ZONE 'UTC'
                      WHERE id = $1`, [runId, globalWorking, me(req)]);
                // The lines are rebuilt; the adjustments are NOT. Somebody
                // entered those by hand with a reason, and regenerating the
                // month is not a reason to throw them away.
                await client.query(`DELETE FROM payroll_lines WHERE run_id = $1`, [runId]);
            } else {
                runId = (await client.query(
                    `INSERT INTO payroll_runs (month, working_days, generated_by)
                     VALUES ($1::date, $2, $3) RETURNING id`,
                    [first, globalWorking, me(req)])).rows[0].id;
            }

            for (const person of people) {
                const weeklyOffs = month.perEmployee.get(person.employee_id)
                                ?? month.global.weekly_offs;
                const workingDays = workingDaysOfMonth({
                    monthStart: month.first, monthEnd: month.last,
                    weeklyOffs, holidays: month.holidays, isNonWorkingDay,
                });

                const presentDays = month.present.get(person.employee_id) || new Set();
                const theirLeave = month.leave.get(person.employee_id) || new Map();

                let present = 0, paidLeave = 0, unpaidLeave = 0, absent = 0;
                for (const day of workingDays) {
                    // SOMEBODY WHO JOINED ON THE 20th WAS NOT ABSENT ON THE
                    // 5th. Without this every new hire's first payslip opens
                    // with a fortnight of deductions.
                    if (person.joined_on && day < person.joined_on) continue;

                    const onLeave = theirLeave.get(day);
                    if (onLeave) {
                        if (onLeave.type === "UNPAID") unpaidLeave += onLeave.cost;
                        else paidLeave += onLeave.cost;
                        // A half day of leave still leaves half a day to
                        // work, and if they worked it they were present.
                        if (onLeave.cost < 1 && presentDays.has(day)) present += 0.5;
                        continue;
                    }
                    if (presentDays.has(day)) present += 1;
                    else absent += 1;
                }

                const line = calculateLine({
                    gross: Number(person.gross_monthly) || 0,
                    workingDays: workingDays.length,
                    presentDays: present,
                    paidLeaveDays: paidLeave,
                    unpaidLeaveDays: unpaidLeave,
                    absentDays: absent,
                    // Overtime is entered by hand after generation — the
                    // owner's decision, and the honest one: hours at a desk
                    // are not the same thing as overtime somebody approved.
                    overtimeHours: 0,
                    overtimeRate: Number(person.overtime_hourly) || 0,
                });

                await client.query(
                    `INSERT INTO payroll_lines
                         (run_id, employee_id, gross_monthly, working_days,
                          present_days, paid_leave_days, unpaid_leave_days,
                          absent_days, per_day, absent_deduction,
                          unpaid_deduction, overtime_hours, overtime_rate,
                          overtime_amount, net_before_adjustments)
                     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)`,
                    [runId, person.employee_id, line.gross, line.working_days,
                     line.present_days, line.paid_leave_days, line.unpaid_leave_days,
                     line.absent_days, line.per_day, line.absent_deduction,
                     line.unpaid_deduction, 0, line.overtime_rate, 0,
                     line.net_before_adjustments]);
            }

            await client.query("COMMIT");
        } catch (error) {
            await client.query("ROLLBACK").catch(() => {});
            throw error;
        } finally {
            client.release();
        }

        await writeAudit(me(req), `PAYROLL GENERATED : ${req.body.month} (draft)`);
        return res.json({ success: true, run_id: runId, month: req.body.month });
    } catch (error) {
        return serverError(res, req, error);
    }
};

/** The whole of a month, lines and adjustments together. */
async function loadRun(month) {
    const run = (await pool.query(
        `SELECT r.id, TO_CHAR(r.month,'YYYY-MM') AS month, r.status,
                r.working_days, r.generated_at, r.finalized_at,
                COALESCE(g.full_name, g.username) AS generated_by_name,
                COALESCE(f.full_name, f.username) AS finalized_by_name
           FROM payroll_runs r
           LEFT JOIN employees g ON g.employee_id = r.generated_by
           LEFT JOIN employees f ON f.employee_id = r.finalized_by
          WHERE r.month = $1::date`, [month])).rows[0];
    if (!run) return null;

    const lines = (await pool.query(
        // LEFT JOIN, AND A FALLBACK NAME.
        //
        // An inner join here meant that the moment somebody left the company
        // their line vanished from a run that was already finalised — the row
        // survives now (see the migration that dropped the cascade), so the
        // read has to survive too, or the total on screen stops matching the
        // total in the table.
        //
        // retired_employee_ids holds the name against the retired id. This is
        // the same answer the chat gives for a message from somebody who has
        // left: show who it was, and say they are gone.
        `SELECT l.*,
                COALESCE(e.full_name, e.username, r.full_name, l.employee_id)
                    AS employee_name,
                e.designation,
                (e.employee_id IS NULL) AS former_employee
           FROM payroll_lines l
           LEFT JOIN employees e ON e.employee_id = l.employee_id
           LEFT JOIN retired_employee_ids r ON r.employee_id = l.employee_id
          WHERE l.run_id = $1
          ORDER BY l.employee_id`, [run.id])).rows;

    const adjustments = (await pool.query(
        `SELECT a.*, COALESCE(c.full_name, c.username) AS created_by_name
           FROM payroll_adjustments a
           LEFT JOIN employees c ON c.employee_id = a.created_by
          WHERE a.run_id = $1
          ORDER BY a.created_at`, [run.id])).rows;

    const byEmployee = new Map();
    for (const adjustment of adjustments) {
        if (!byEmployee.has(adjustment.employee_id)) byEmployee.set(adjustment.employee_id, []);
        byEmployee.get(adjustment.employee_id).push(adjustment);
    }

    for (const line of lines) {
        line.adjustments = byEmployee.get(line.employee_id) || [];
        line.adjustments_total = money(
            line.adjustments.reduce((sum, a) => sum + Number(a.amount), 0));
        // Net is the frozen figure plus whatever has been adjusted since.
        // That is what makes an adjustment the only way to change a finalised
        // month without rewriting it.
        line.net_pay = money(Number(line.net_before_adjustments) + line.adjustments_total);
    }

    return { run, lines };
}

/** GET /api/admin/payroll/:month */
exports.getRun = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const first = monthStart(req.params.month);
    if (!first) return fail(res, 400, "Month must be YYYY-MM.");
    try {
        const found = await loadRun(first);
        if (!found) return res.json({ success: true, run: null, lines: [] });

        const totals = found.lines.reduce((sum, l) => ({
            gross: money(sum.gross + Number(l.gross_monthly)),
            deductions: money(sum.deductions + Number(l.absent_deduction)
                              + Number(l.unpaid_deduction)),
            overtime: money(sum.overtime + Number(l.overtime_amount)),
            adjustments: money(sum.adjustments + Number(l.adjustments_total)),
            net: money(sum.net + Number(l.net_pay)),
        }), { gross: 0, deductions: 0, overtime: 0, adjustments: 0, net: 0 });

        return res.json({ success: true, ...found, totals });
    } catch (error) {
        return serverError(res, req, error);
    }
};

/** GET /api/admin/payroll — which months exist, and what they came to. */
exports.listRuns = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    try {
        const rows = await pool.query(
            `SELECT TO_CHAR(r.month,'YYYY-MM') AS month, r.status, r.working_days,
                    r.generated_at, r.finalized_at,
                    COUNT(l.id)::int AS employees,
                    COALESCE(SUM(l.net_before_adjustments), 0) AS net_before_adjustments,
                    COALESCE((SELECT SUM(amount) FROM payroll_adjustments
                               WHERE run_id = r.id), 0) AS adjustments_total
               FROM payroll_runs r
               LEFT JOIN payroll_lines l ON l.run_id = r.id
              GROUP BY r.id
              ORDER BY r.month DESC
              LIMIT 36`);
        for (const row of rows.rows) {
            row.total_payout = money(Number(row.net_before_adjustments)
                                   + Number(row.adjustments_total));
        }
        return res.json({ success: true, data: rows.rows });
    } catch (error) {
        return serverError(res, req, error);
    }
};

/**
 * POST /api/admin/payroll/:month/finalize — the month stops moving.
 *
 * After this the figures cannot be regenerated. Anything that needs to change
 * is an adjustment, with a reason and a name against it.
 */
exports.finalize = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const first = monthStart(req.params.month);
    if (!first) return fail(res, 400, "Month must be YYYY-MM.");

    try {
        const run = (await pool.query(
            `SELECT id, status FROM payroll_runs WHERE month = $1::date`, [first])).rows[0];
        if (!run) return fail(res, 404, "That month has not been generated yet.");
        if (run.status === "FINALIZED") {
            return fail(res, 409, "That month is already finalised.");
        }
        const lines = Number((await pool.query(
            `SELECT COUNT(*)::int AS n FROM payroll_lines WHERE run_id = $1`,
            [run.id])).rows[0].n);
        if (lines === 0) {
            return fail(res, 400, "There is nothing in that month to finalise.");
        }

        await pool.query(
            `UPDATE payroll_runs SET status = 'FINALIZED', finalized_by = $2,
                    finalized_at = NOW() AT TIME ZONE 'UTC' WHERE id = $1`,
            [run.id, me(req)]);

        await writeAudit(me(req), `PAYROLL FINALIZED : ${req.params.month}, ${lines} employees`);

        // Everybody with an email is told their payslip is ready. A failure
        // here does not un-finalise anything — see leave.controller for the
        // same reasoning.
        if (mailer.isConfigured()) {
            const people = (await pool.query(
                // This one stays an INNER JOIN on purpose: it is the list of
                // people to EMAIL a payslip to, and somebody who has left the
                // company has no account and no address to send to.
                `SELECT e.email, COALESCE(e.full_name, e.username) AS name,
                        l.net_before_adjustments
                   FROM payroll_lines l JOIN employees e ON e.employee_id = l.employee_id
                  WHERE l.run_id = $1 AND e.email IS NOT NULL`, [run.id])).rows;
            for (const person of people) {
                mailer.send({
                    to: person.email,
                    subject: `Your payslip for ${req.params.month} is ready`,
                    text: `Hello ${person.name},\n\n`
                        + `Your payslip for ${req.params.month} is available in `
                        + `Amaze Connect, under My Payroll.\n`,
                }).catch((error) => writeAudit(me(req),
                    `PAYSLIP EMAIL FAILED : ${person.email} — ${error.message}`));
            }
        }

        return res.json({ success: true, month: req.params.month, employees: lines });
    } catch (error) {
        return serverError(res, req, error);
    }
};

// ──────────────────────────────────────────────────────────  adjustments

/** POST /api/admin/payroll/:month/adjustments */
exports.addAdjustment = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const first = monthStart(req.params.month);
    if (!first) return fail(res, 400, "Month must be YYYY-MM.");

    const { employee_id, kind, amount, reason } = req.body || {};
    if (!employee_id) return fail(res, 400, "Which employee?");
    if (!KINDS.includes(String(kind))) {
        return fail(res, 400, `Kind must be one of ${KINDS.join(", ")}.`);
    }
    const value = Number(amount);
    if (!Number.isFinite(value) || value === 0) {
        return fail(res, 400, "An adjustment needs an amount that is not zero.");
    }
    if (!String(reason || "").trim()) {
        // REQUIRED, ALWAYS. An amount with no explanation is unanswerable a
        // year later, and this is the field somebody reads when they ask why
        // their pay was different.
        return fail(res, 400, "Every adjustment needs a reason — it is what is asked about later.");
    }

    // The sign belongs to the kind, not to whoever typed it. A fine entered
    // as +500 would otherwise pay somebody for being fined.
    const signed = NEGATIVE_KINDS.has(kind)
        ? -Math.abs(value)
        : (kind === "OTHER" ? value : Math.abs(value));

    try {
        const run = (await pool.query(
            `SELECT id FROM payroll_runs WHERE month = $1::date`, [first])).rows[0];
        if (!run) return fail(res, 404, "That month has not been generated yet.");

        const line = await pool.query(
            `SELECT 1 FROM payroll_lines WHERE run_id = $1 AND employee_id = $2`,
            [run.id, employee_id]);
        if (line.rowCount === 0) {
            return fail(res, 400, "That employee is not in that month's payroll.");
        }

        const row = (await pool.query(
            `INSERT INTO payroll_adjustments
                 (run_id, employee_id, kind, amount, reason, created_by)
             VALUES ($1,$2,$3,$4,$5,$6)
             RETURNING id, employee_id, kind, amount, reason, created_at`,
            [run.id, employee_id, kind, signed, String(reason).trim(), me(req)])).rows[0];

        await writeAudit(employee_id,
            `PAYROLL ADJUSTMENT : ${kind} ${signed} for ${req.params.month} `
            + `by ${me(req)} — ${String(reason).trim().slice(0, 80)}`);
        return res.status(201).json({ success: true, adjustment: row });
    } catch (error) {
        return serverError(res, req, error);
    }
};

/** DELETE /api/admin/payroll/adjustments/:id — only while the month is a draft. */
exports.removeAdjustment = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) return fail(res, 400, "Which adjustment?");
    try {
        const found = (await pool.query(
            `SELECT a.id, a.employee_id, a.kind, a.amount, r.status,
                    TO_CHAR(r.month,'YYYY-MM') AS month
               FROM payroll_adjustments a
               JOIN payroll_runs r ON r.id = a.run_id
              WHERE a.id = $1`, [id])).rows[0];
        if (!found) return fail(res, 404, "No such adjustment.");
        if (found.status === "FINALIZED") {
            // A finalised month is a record. Removing a line from it would be
            // rewriting what somebody was told they were paid; the answer is
            // another adjustment the other way, which leaves both visible.
            return fail(res, 409,
                "That month is finalised. Add an opposite adjustment rather than "
                + "removing this one — both stay on the record.");
        }
        await pool.query(`DELETE FROM payroll_adjustments WHERE id = $1`, [id]);
        await writeAudit(found.employee_id,
            `PAYROLL ADJUSTMENT REMOVED : ${found.kind} ${found.amount} `
            + `for ${found.month} by ${me(req)}`);
        return res.json({ success: true });
    } catch (error) {
        return serverError(res, req, error);
    }
};

/** POST /api/admin/payroll/:month/overtime — hours, entered by hand. */
exports.setOvertime = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const first = monthStart(req.params.month);
    if (!first) return fail(res, 400, "Month must be YYYY-MM.");

    const { employee_id, hours } = req.body || {};
    const value = Number(hours);
    if (!Number.isFinite(value) || value < 0) {
        return fail(res, 400, "Overtime hours must be a number, and not negative.");
    }
    if (value > 400) {
        return fail(res, 400, "That is more hours than there are in a working month.");
    }

    try {
        const run = (await pool.query(
            `SELECT id, status FROM payroll_runs WHERE month = $1::date`, [first])).rows[0];
        if (!run) return fail(res, 404, "That month has not been generated yet.");
        if (run.status === "FINALIZED") {
            return fail(res, 409,
                "That month is finalised. Add an adjustment for the overtime instead.");
        }

        const line = (await pool.query(
            `SELECT * FROM payroll_lines WHERE run_id = $1 AND employee_id = $2`,
            [run.id, employee_id])).rows[0];
        if (!line) return fail(res, 400, "That employee is not in that month's payroll.");

        const recalculated = calculateLine({
            gross: Number(line.gross_monthly),
            workingDays: Number(line.working_days),
            presentDays: Number(line.present_days),
            paidLeaveDays: Number(line.paid_leave_days),
            unpaidLeaveDays: Number(line.unpaid_leave_days),
            absentDays: Number(line.absent_days),
            overtimeHours: value,
            overtimeRate: Number(line.overtime_rate),
        });

        await pool.query(
            `UPDATE payroll_lines
                SET overtime_hours = $3, overtime_amount = $4,
                    net_before_adjustments = $5
              WHERE run_id = $1 AND employee_id = $2`,
            [run.id, employee_id, value, recalculated.overtime_amount,
             recalculated.net_before_adjustments]);

        await writeAudit(employee_id,
            `PAYROLL OVERTIME : ${value} hours for ${req.params.month} by ${me(req)}`);
        return res.json({ success: true, line: recalculated });
    } catch (error) {
        return serverError(res, req, error);
    }
};

// ──────────────────────────────────────────────────────── the employee's

/** GET /api/payroll/mine — every payslip of the caller's, and nobody else's. */
exports.mine = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");
    try {
        const rows = await pool.query(
            `SELECT TO_CHAR(r.month,'YYYY-MM') AS month, r.status, r.finalized_at,
                    l.gross_monthly, l.working_days, l.present_days,
                    l.paid_leave_days, l.unpaid_leave_days, l.absent_days,
                    l.absent_deduction, l.unpaid_deduction,
                    l.overtime_hours, l.overtime_amount,
                    l.net_before_adjustments,
                    COALESCE((SELECT SUM(amount) FROM payroll_adjustments a
                               WHERE a.run_id = r.id AND a.employee_id = l.employee_id), 0)
                      AS adjustments_total
               FROM payroll_lines l
               JOIN payroll_runs r ON r.id = l.run_id
              WHERE l.employee_id = $1
                -- A DRAFT IS NOT SHOWN. It is working material that may still
                -- change, and somebody seeing a number that then moves has
                -- been told something untrue.
                AND r.status = 'FINALIZED'
              ORDER BY r.month DESC`, [employeeId]);
        for (const row of rows.rows) {
            row.net_pay = money(Number(row.net_before_adjustments)
                              + Number(row.adjustments_total));
        }
        return res.json({ success: true, data: rows.rows });
    } catch (error) {
        return serverError(res, req, error);
    }
};

const escapeHtml = (value) => String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

/**
 * GET /api/payroll/payslip/:month — the payslip itself, as a page.
 *
 * HTML rather than a PDF. It opens in a browser, prints to paper or to PDF
 * with the browser's own dialog, and needs no library — a PDF generator would
 * be a new dependency on the server for something every machine can already
 * do.
 *
 * An employee gets their own. An admin may ask for somebody else's by adding
 * ?employee_id=, which is the only place in this file that takes one, and it
 * is checked.
 */
exports.payslip = async (req, res) => {
    const caller = me(req);
    if (!caller) return fail(res, 401, "Unauthenticated");

    const wanted = req.query.employee_id ? String(req.query.employee_id) : caller;
    if (wanted !== caller && !isAdmin(req)) {
        return fail(res, 403, "That is not your payslip.");
    }

    const first = monthStart(req.params.month);
    if (!first) return fail(res, 400, "Month must be YYYY-MM.");

    try {
        const found = await loadRun(first);
        if (!found) return fail(res, 404, "That month has not been generated.");
        // AN EMPLOYEE NEVER SEES A DRAFT — the figures may still move. An
        // admin may, because reviewing the draft is their job.
        if (found.run.status !== "FINALIZED" && !isAdmin(req)) {
            return fail(res, 404, "That payslip is not ready yet.");
        }

        const line = found.lines.find((l) => l.employee_id === wanted);
        if (!line) return fail(res, 404, "No payslip for that month.");

        const settings = Object.fromEntries((await pool.query(
            `SELECT key, value FROM app_settings
              WHERE key IN ('company_name','company_address','payroll_currency')`
        )).rows.map((r) => [r.key, r.value]));
        const currency = settings.payroll_currency || "₹";
        const amount = (value) => `${currency}${Number(value).toLocaleString("en-IN",
            { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

        const employee = (await pool.query(
            `SELECT employee_id, COALESCE(full_name, username) AS name, designation,
                    department, TO_CHAR(joining_date,'YYYY-MM-DD') AS joining_date
               FROM employees WHERE employee_id = $1`, [wanted])).rows[0];

        const rows = (label, value, negative = false) => `
            <tr><td style="padding:7px 0;color:#475569;">${escapeHtml(label)}</td>
                <td style="padding:7px 0;text-align:right;font-weight:600;
                           color:${negative ? "#b91c1c" : "#0f172a"};">
                  ${negative ? "−" : ""}${escapeHtml(amount(Math.abs(value)))}</td></tr>`;

        const adjustmentRows = line.adjustments.map((a) => `
            <tr><td style="padding:7px 0;color:#475569;">
                  ${escapeHtml(a.kind.charAt(0) + a.kind.slice(1).toLowerCase())}
                  <span style="color:#94a3b8;font-size:11px;">
                    — ${escapeHtml(a.reason)}</span></td>
                <td style="padding:7px 0;text-align:right;font-weight:600;
                           color:${Number(a.amount) < 0 ? "#b91c1c" : "#15803d"};">
                  ${Number(a.amount) < 0 ? "−" : "+"}${escapeHtml(amount(Math.abs(a.amount)))}
                </td></tr>`).join("");

        const html = `<!doctype html>
<html><head><meta charset="utf-8">
<title>Payslip ${escapeHtml(found.run.month)} — ${escapeHtml(employee.name)}</title>
<style>
  @media print { .noprint { display:none; } body { background:#fff; } }
  body { margin:0; background:#f1f5f9; font-family:-apple-system,Segoe UI,
         Roboto,Helvetica,Arial,sans-serif; color:#0f172a; }
  .sheet { max-width:760px; margin:24px auto; background:#fff; border:1px solid #e2e8f0;
           border-radius:12px; overflow:hidden; }
  .head { padding:22px 28px; background:#0f172a; color:#fff; }
  .body { padding:24px 28px; }
  .grid { display:flex; gap:32px; flex-wrap:wrap; margin-bottom:22px; }
  .grid div { min-width:180px; }
  .k { font-size:11px; color:#64748b; text-transform:uppercase; letter-spacing:.5px; }
  .v { font-size:14px; font-weight:600; margin-top:2px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  .section { margin-top:18px; font-size:11px; font-weight:800; letter-spacing:1px;
             color:#64748b; border-bottom:1px solid #e2e8f0; padding-bottom:6px; }
  .net { margin-top:18px; padding:14px 16px; background:#f0fdf4; border:1px solid #bbf7d0;
         border-radius:10px; display:flex; justify-content:space-between;
         align-items:center; }
</style></head>
<body>
<div class="noprint" style="max-width:760px;margin:16px auto 0;text-align:right;">
  <button onclick="window.print()" style="padding:8px 16px;border-radius:8px;
          border:1px solid #cbd5e1;background:#fff;cursor:pointer;font-size:13px;">
    Print or save as PDF</button>
</div>
<div class="sheet">
  <div class="head">
    <div style="font-size:17px;font-weight:700;">
      ${escapeHtml(settings.company_name || "Amaze Internet")}</div>
    <div style="font-size:12px;color:#94a3b8;margin-top:2px;">
      ${escapeHtml(settings.company_address || "")}</div>
    <div style="font-size:13px;margin-top:10px;">
      Payslip · ${escapeHtml(found.run.month)}</div>
  </div>
  <div class="body">
    <div class="grid">
      <div><div class="k">Employee</div><div class="v">${escapeHtml(employee.name)}</div></div>
      <div><div class="k">Employee ID</div><div class="v">${escapeHtml(employee.employee_id)}</div></div>
      <div><div class="k">Designation</div><div class="v">${escapeHtml(employee.designation || "—")}</div></div>
      <div><div class="k">Department</div><div class="v">${escapeHtml(employee.department || "—")}</div></div>
      <div><div class="k">Joined</div><div class="v">${escapeHtml(employee.joining_date || "—")}</div></div>
    </div>

    <div class="grid">
      <div><div class="k">Working days</div><div class="v">${escapeHtml(line.working_days)}</div></div>
      <div><div class="k">Present</div><div class="v">${escapeHtml(line.present_days)}</div></div>
      <div><div class="k">Leave (paid)</div><div class="v">${escapeHtml(line.paid_leave_days)}</div></div>
      <div><div class="k">Leave (unpaid)</div><div class="v">${escapeHtml(line.unpaid_leave_days)}</div></div>
      <div><div class="k">Absent</div><div class="v">${escapeHtml(line.absent_days)}</div></div>
    </div>

    <div class="section">EARNINGS</div>
    <table>
      ${rows("Monthly gross", line.gross_monthly)}
      ${Number(line.overtime_hours) > 0
        ? rows(`Overtime (${line.overtime_hours} h at ${amount(line.overtime_rate)})`,
               line.overtime_amount)
        : ""}
    </table>

    ${Number(line.absent_deduction) || Number(line.unpaid_deduction) ? `
    <div class="section">DEDUCTIONS</div>
    <table>
      ${Number(line.unpaid_deduction)
        ? rows(`Unpaid leave (${line.unpaid_leave_days} days at ${amount(line.per_day)})`,
               line.unpaid_deduction, true) : ""}
      ${Number(line.absent_deduction)
        ? rows(`Absent (${line.absent_days} days at ${amount(line.per_day)})`,
               line.absent_deduction, true) : ""}
    </table>` : ""}

    ${adjustmentRows ? `
    <div class="section">ADJUSTMENTS</div>
    <table>${adjustmentRows}</table>` : ""}

    <div class="net">
      <div style="font-size:13px;color:#166534;font-weight:700;">NET PAY</div>
      <div style="font-size:22px;font-weight:800;color:#166534;">
        ${escapeHtml(amount(line.net_pay))}</div>
    </div>

    <p style="margin-top:20px;font-size:11px;color:#94a3b8;line-height:1.6;">
      Computer generated — no signature required.
      ${found.run.status === "FINALIZED"
        ? `Finalised ${escapeHtml(String(found.run.finalized_at).slice(0, 10))}.`
        : "DRAFT — these figures may still change."}
    </p>
  </div>
</div>
</body></html>`;

        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.setHeader("Content-Disposition",
            `inline; filename="payslip-${found.run.month}-${wanted}.html"`);
        return res.send(html);
    } catch (error) {
        return serverError(res, req, error);
    }
};

// ──────────────────────────────────────────────────────────────  reports

/** GET /api/admin/payroll/:month/summary — the month, totalled. */
exports.summary = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const first = monthStart(req.params.month);
    if (!first) return fail(res, 400, "Month must be YYYY-MM.");
    try {
        const found = await loadRun(first);
        if (!found) return fail(res, 404, "That month has not been generated.");

        const totals = found.lines.reduce((sum, l) => ({
            employees: sum.employees + 1,
            gross: money(sum.gross + Number(l.gross_monthly)),
            unpaid_leave_deduction: money(sum.unpaid_leave_deduction
                                          + Number(l.unpaid_deduction)),
            absent_deduction: money(sum.absent_deduction + Number(l.absent_deduction)),
            overtime_hours: money(sum.overtime_hours + Number(l.overtime_hours)),
            overtime_amount: money(sum.overtime_amount + Number(l.overtime_amount)),
            adjustments: money(sum.adjustments + Number(l.adjustments_total)),
            net: money(sum.net + Number(l.net_pay)),
        }), { employees: 0, gross: 0, unpaid_leave_deduction: 0, absent_deduction: 0,
              overtime_hours: 0, overtime_amount: 0, adjustments: 0, net: 0 });

        return res.json({
            success: true,
            month: found.run.month,
            status: found.run.status,
            working_days: found.run.working_days,
            totals,
            // The three reports asked for, from one pass rather than three
            // queries that could disagree.
            leave_deductions: found.lines
                .filter((l) => Number(l.unpaid_deduction) > 0)
                .map((l) => ({ employee_id: l.employee_id, name: l.employee_name,
                               days: l.unpaid_leave_days, amount: l.unpaid_deduction })),
            overtime: found.lines
                .filter((l) => Number(l.overtime_hours) > 0)
                .map((l) => ({ employee_id: l.employee_id, name: l.employee_name,
                               hours: l.overtime_hours, amount: l.overtime_amount })),
        });
    } catch (error) {
        return serverError(res, req, error);
    }
};
