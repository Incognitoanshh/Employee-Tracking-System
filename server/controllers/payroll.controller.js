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
const { isNonWorkingDay, classifyLogin } = require("../utils/attendance_status");
const { calculateLine, workingDaysOfMonth, splitCTC, statutoryFor,
        money } = require("../utils/payroll_math");
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
                    e.department,
                    s.gross_monthly, s.overtime_hourly, s.ctc_annual,
                    s.epf_enabled, s.esi_enabled, s.pt_enabled,
                    s.effective_from::text, s.remarks,
                    -- Which salary version is in force, so its own split can
                    -- be looked up below. Selected inside the LATERAL and not
                    -- carried out of it, every row came back with no id and
                    -- every person with an empty split.
                    s.salary_id
               FROM employees e
               LEFT JOIN LATERAL (
                   -- The salary in force TODAY: the most recent one that has
                   -- already taken effect. A rise dated next month is stored
                   -- and does not show here until it applies.
                   SELECT id AS salary_id, gross_monthly, overtime_hourly,
                          effective_from, remarks,
                          ctc_annual, epf_enabled, esi_enabled, pt_enabled
                     FROM employee_salaries
                    WHERE employee_id = e.employee_id
                      AND effective_from <= (NOW() AT TIME ZONE 'Asia/Kolkata')::date
                    ORDER BY effective_from DESC LIMIT 1
               ) s ON TRUE
              WHERE e.role <> 'super_admin'
              ORDER BY e.employee_id`);

        // EACH PERSON'S OWN SPLIT, AS SAVED — rules as well as amounts.
        //
        // A component can now be set by hand ("dono option rakho — auto bhi,
        // aur zaroorat pade to haath se"), which makes the company template
        // only the STARTING point. The page used to rebuild every person's
        // split from that template whenever they were opened, so an override
        // would have been saved, shown as gone the next time, and then erased
        // by the next save — which would send the template again. The saved
        // components are the truth about a salary; they come down with it.
        const salaryIds = rows.rows.map((r) => r.salary_id).filter(Boolean);
        const splitBy = new Map();
        if (salaryIds.length) {
            for (const part of (await pool.query(
                `SELECT salary_id, name, rule, value, monthly
                   FROM salary_components
                  WHERE salary_id = ANY($1)
                  ORDER BY salary_id, position, id`, [salaryIds])).rows) {
                if (!splitBy.has(part.salary_id)) splitBy.set(part.salary_id, []);
                splitBy.get(part.salary_id).push({
                    name: part.name, rule: part.rule,
                    value: Number(part.value), monthly: Number(part.monthly),
                });
            }
        }
        for (const row of rows.rows) {
            row.components = splitBy.get(row.salary_id) || [];
        }

        // THE COMPANY'S SPLIT, SENT WITH THE LIST.
        //
        // The panel previews what a CTC divides into as it is typed, and it
        // was doing that from a copy of this arrangement written into the
        // client. The two agreed on the day it was written and nothing kept
        // them agreeing: change the policy here and the preview would go on
        // showing the old percentages while the server stored the new ones.
        // A preview that can disagree with what gets saved is worse than no
        // preview, so the client is given the real thing.
        let template = [];
        try {
            const stored = (await pool.query(
                `SELECT value FROM app_settings WHERE key = 'salary_template'`
            )).rows[0];
            template = JSON.parse(stored?.value || "[]");
        } catch (_) {
            template = [];
        }
        return res.json({ success: true, data: rows.rows, template });
    } catch (error) {
        return serverError(res, req, error);
    }
};

/** POST /api/admin/payroll/salaries — set one, from a date. */
exports.setSalary = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const { employee_id, gross_monthly, overtime_hourly, effective_from,
            remarks, ctc_annual, components,
            epf_enabled, esi_enabled, pt_enabled } = req.body || {};

    if (!employee_id) return fail(res, 400, "Which employee?");

    // ── a CTC, split into the parts a payslip prints ────────────────────
    //
    // THE MONTHLY GROSS IS THE SUM OF THE PARTS, and that is the bridge that
    // makes this safe: everything downstream — the payroll run, the payslip,
    // every frozen line already written — goes on reading gross_monthly and
    // does not know or care that it now has a structure behind it. Set a CTC
    // and the gross follows from it; set a gross directly, as before, and
    // nothing about components exists.
    let split = null;
    let derivedGross = null;
    if (ctc_annual !== undefined && ctc_annual !== null && ctc_annual !== "") {
        const annual = Number(ctc_annual);
        if (!Number.isFinite(annual) || annual < 0) {
            return fail(res, 400, "The annual CTC must be a number, and not negative.");
        }
        if (annual > 1000000000) {
            return fail(res, 400, "That is more than a hundred crore a year — check the figure.");
        }

        let template = components;
        if (!Array.isArray(template) || template.length === 0) {
            // The company's default split, unless the caller sent their own.
            const stored = (await pool.query(
                `SELECT value FROM app_settings WHERE key = 'salary_template'`
            )).rows[0];
            try {
                template = JSON.parse(stored?.value || "[]");
            } catch (_) {
                template = [];
            }
        }
        split = splitCTC(annual / 12, template);
        derivedGross = money(split.reduce((sum, row) => sum + row.monthly, 0));

        // A SPLIT THAT COMES TO MORE THAN THE CTC IS REFUSED, not saved.
        //
        // Components can now be set by hand, and the balance row cannot go
        // below zero — so figures that overshoot do not fail on their own,
        // they make the monthly gross bigger than the CTC it was meant to
        // divide: a salary stored as six lakh a year that pays 55,500 a month.
        // The panel stops this before sending it; this is the same rule for
        // anything that reaches the API another way. A paisa of rounding is
        // allowed for, and nothing more.
        const monthlyCtc = money(annual / 12);
        if (derivedGross - monthlyCtc > 0.01) {
            return fail(res, 400,
                `The components come to ${derivedGross} a month, which is more than `
                + `the CTC allows (${monthlyCtc}). Lower one of them, or raise the CTC.`);
        }
    }

    const gross = derivedGross !== null ? derivedGross : Number(gross_monthly);
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
    // Optional, and capped rather than refused: somebody explaining a rise
    // should not lose what they typed to a length limit they were not told
    // about. 2000 characters is far more than a reason needs and far less
    // than anything that would strain a row.
    const note = remarks === undefined || remarks === null
        ? null : String(remarks).trim().slice(0, 2000) || null;

    // THE CONNECTION IS TAKEN OUTSIDE THE try, AND RELEASED IN ONE PLACE.
    //
    // It used to be `const client` declared INSIDE the try, with the catch
    // doing `client.query("ROLLBACK")` and `client.release()`. A const is
    // scoped to its block, so in the catch `client` did not exist: any error
    // part-way through a save threw ReferenceError out of the error handler,
    // and the connection was never rolled back and never released.
    //
    // Measured, not inferred: a save made to fail inside the transaction left
    // the pool at 3 connections with 2 idle — one checked out for good, holding
    // an aborted transaction — and pool.end() then waited on it for ever. The
    // pool is ten connections, so ten failed salary saves would have been the
    // whole server hanging on every request that needs the database.
    //
    // Now: one connect before the try, and exactly one release, in finally,
    // whichever way out the request takes.
    const client = await pool.connect();
    // WRITTEN ONLY ONCE THE SAVE HAS COMMITTED. writeAudit goes through the
    // pool, outside this transaction, so an entry written in the middle of it
    // survived a rollback: the log said a salary was set and the drafts were
    // refreshed when nothing had been saved at all. An audit trail that can
    // describe things that did not happen is worse than none.
    const audits = [];
    try {
        await client.query("BEGIN");

        const target = await client.query(
            `SELECT role, COALESCE(full_name, username) AS name FROM employees
              WHERE employee_id = $1`, [employee_id]);
        if (target.rowCount === 0) {
            await client.query("ROLLBACK");
            return fail(res, 404, "No such employee.");
        }
        if (target.rows[0].role === "super_admin") {
            await client.query("ROLLBACK");
            return fail(res, 400, "The super admin is the owner, not an employee on payroll.");
        }

        const row = (await client.query(
            `INSERT INTO employee_salaries
                 (employee_id, gross_monthly, overtime_hourly, effective_from,
                  created_by, remarks, ctc_annual,
                  epf_enabled, esi_enabled, pt_enabled)
             VALUES ($1, $2, $3,
                     COALESCE($4::date, DATE_TRUNC('month',
                              (NOW() AT TIME ZONE 'Asia/Kolkata'))::date), $5, $6,
                     $7, $8, $9, $10)
             ON CONFLICT (employee_id, effective_from)
             DO UPDATE SET gross_monthly = EXCLUDED.gross_monthly,
                           overtime_hourly = EXCLUDED.overtime_hourly,
                           created_by = EXCLUDED.created_by,
                           -- An edit that says nothing keeps what the last one
                           -- said. Blanking the reason for a salary because
                           -- somebody corrected a typo in the figure loses the
                           -- only record of why the figure exists.
                           remarks = COALESCE(EXCLUDED.remarks, employee_salaries.remarks),
                           ctc_annual  = COALESCE(EXCLUDED.ctc_annual, employee_salaries.ctc_annual),
                           epf_enabled = EXCLUDED.epf_enabled,
                           esi_enabled = EXCLUDED.esi_enabled,
                           pt_enabled  = EXCLUDED.pt_enabled,
                           created_at = NOW() AT TIME ZONE 'UTC'
             RETURNING id, employee_id, gross_monthly, overtime_hourly,
                       effective_from::text, remarks, ctc_annual,
                       epf_enabled, esi_enabled, pt_enabled`,
            [employee_id, gross, overtime, from, me(req), note,
             derivedGross !== null ? Number(ctc_annual) : null,
             Boolean(epf_enabled), Boolean(esi_enabled), Boolean(pt_enabled)])).rows[0];

        // THE COMPONENTS BELONG TO THIS VERSION OF THE SALARY. Replaced
        // wholesale rather than merged: a split is one arrangement, and half
        // of an old one beside half of a new one is not an arrangement at all.
        if (split) {
            await client.query(`DELETE FROM salary_components WHERE salary_id = $1`,
                             [row.id]);
            for (const [index, part] of split.entries()) {
                await client.query(
                    `INSERT INTO salary_components
                         (salary_id, position, name, rule, value, monthly)
                     VALUES ($1,$2,$3,$4,$5,$6)`,
                    [row.id, index, part.name, part.rule, part.value, part.monthly]);
            }
            row.components = split;
        }

        // A draft is deliberately mutable working material.  Its lines are
        // snapshots, so changing the salary alone cannot make an existing
        // draft reflect the new gross, statutory deductions, or component
        // split. Rebuild every *affected* draft now; finalised payroll is
        // intentionally excluded because it is a statement of what was paid.
        // generateDraft preserves manually entered adjustments and deductions.
        const drafts = (await client.query(
            `SELECT TO_CHAR(month, 'YYYY-MM') AS month
               FROM payroll_runs
              WHERE status = 'DRAFT'
                AND (DATE_TRUNC('month', month) + INTERVAL '1 month - 1 day')::date
                    >= $1::date
              ORDER BY month`, [row.effective_from])).rows;
        for (const draft of drafts) {
            await generateDraft(`${draft.month}-01`, me(req), client);
            audits.push([me(req),
                `PAYROLL DRAFT REFRESHED : ${draft.month} after salary update for ${employee_id}`]);
        }

        audits.push([employee_id,
            `SALARY SET : ${gross} per month from ${row.effective_from} by ${me(req)}`
            + (note ? ` — ${note}` : "")]);

        await client.query("COMMIT");

        for (const [who, what] of audits) await writeAudit(who, what);
        return res.json({ success: true, salary: row,
                          refreshed_drafts: drafts.map((draft) => draft.month) });
    } catch (error) {
        await client.query("ROLLBACK").catch(() => {});
        // A draft finalised by somebody else between being listed above and
        // being rebuilt: the whole save is rolled back, and the reason given
        // is the real one rather than "internal server error".
        if (error.status === 409) return fail(res, 409, error.message);
        return serverError(res, req, error);
    } finally {
        client.release();
    }
};

/** GET /api/admin/payroll/salaries/:employee_id — the history of one person's pay. */
exports.salaryHistory = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    try {
        const rows = await pool.query(
            `SELECT s.id, s.gross_monthly, s.overtime_hourly,
                    s.effective_from::text, s.created_at, s.remarks,
                    s.ctc_annual, s.epf_enabled, s.esi_enabled, s.pt_enabled,
                    COALESCE(c.full_name, c.username) AS created_by_name,
                    -- The parts this salary was made of, in the order they
                    -- are printed. An older salary has none, and an empty
                    -- array is the honest answer for it.
                    COALESCE((
                        SELECT json_agg(json_build_object(
                                   'name', k.name, 'rule', k.rule,
                                   'value', k.value, 'monthly', k.monthly)
                                   ORDER BY k.position)
                          FROM salary_components k WHERE k.salary_id = s.id
                    ), '[]'::json) AS components
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
async function gatherMonth(monthFirst, txClient = null) {
    const queryClient = txClient || pool;
    const bounds = (await queryClient.query(
        `SELECT $1::date AS first,
                (DATE_TRUNC('month', $1::date) + INTERVAL '1 month - 1 day')::date AS last`,
        [monthFirst])).rows[0];
    const first = String(bounds.first).slice(0, 10);
    const last = String(bounds.last).slice(0, 10);

    const global = (await queryClient.query(
        `SELECT weekly_offs, shift_start, shift_end, late_grace_minutes
           FROM employee_configs WHERE employee_id IS NULL`
    )).rows[0] || {};

    const configs = new Map((await queryClient.query(
        `SELECT employee_id, weekly_offs, shift_start, shift_end, late_grace_minutes
           FROM employee_configs WHERE employee_id IS NOT NULL`
    )).rows.map((r) => [r.employee_id, r]));
    const perEmployee = new Map(
        [...configs].map(([id, row]) => [id, row.weekly_offs]));

    /**
     * One person's shift, inheriting FIELD BY FIELD from the global row.
     *
     * An empty shift on somebody's own config row means "use the global one",
     * not "this person has no shift" — saving any unrelated setting creates
     * that row with the shift columns empty. Reading it as an override is what
     * emptied the Attendance table's shift column; payroll must not repeat it,
     * or a whole company would come out never late.
     */
    const shiftFor = (employeeId) => {
        const own = configs.get(employeeId) || {};
        return {
            shiftStart: own.shift_start ?? global.shift_start ?? null,
            shiftEnd:   own.shift_end   ?? global.shift_end   ?? null,
            grace: own.late_grace_minutes ?? global.late_grace_minutes ?? 10,
        };
    };

    // THE LATENESS POLICY, as it stands at the moment of generation. Read once
    // and frozen into the run like every other component: turning the policy
    // on in October must not retroactively fine anybody for August.
    const settings = new Map((await queryClient.query(
        `SELECT key, value FROM app_settings
          WHERE key IN ('late_deduction_mode', 'late_deduction_free_days',
                        'payroll_hours_per_day')`)).rows.map((r) => [r.key, r.value]));
    const latePolicy = {
        mode: String(settings.get("late_deduction_mode") || "NONE"),
        freeDays: Number(settings.get("late_deduction_free_days") || 0) || 0,
        hoursPerDay: Number(settings.get("payroll_hours_per_day") || 8) || 8,
    };

    const holidays = new Set((await queryClient.query(
        `SELECT TO_CHAR(holiday_date,'YYYY-MM-DD') AS d FROM holidays
          WHERE holiday_date BETWEEN $1::date AND $2::date`, [first, last]))
        .rows.map((r) => r.d));

    // Attendance: which days each person actually turned up, and how far into
    // the shift the FIRST sign-in of each day was.
    //
    // The first one, not any of them: somebody whose connection drops and who
    // signs back in at two o'clock has not arrived five hours late. That rule
    // already governs the Attendance table — this reads the same MIN(login)
    // per day so the two cannot come to different conclusions about the same
    // morning.
    const present = new Map();
    const firstLogin = new Map();
    for (const row of (await queryClient.query(
        `SELECT employee_id, ${istDate("login_time")}::text AS d,
                MIN(login_time) AS first_login,
                EXTRACT(HOUR   FROM (MIN(login_time) AT TIME ZONE 'UTC')
                                     AT TIME ZONE 'Asia/Kolkata') * 60
              + EXTRACT(MINUTE FROM (MIN(login_time) AT TIME ZONE 'UTC')
                                     AT TIME ZONE 'Asia/Kolkata') AS ist_minutes
           FROM attendance
          WHERE ${istDate("login_time")} BETWEEN $1::date AND $2::date
          GROUP BY employee_id, ${istDate("login_time")}`, [first, last])).rows) {
        if (!present.has(row.employee_id)) present.set(row.employee_id, new Set());
        present.get(row.employee_id).add(row.d);

        if (!firstLogin.has(row.employee_id)) firstLogin.set(row.employee_id, new Map());
        firstLogin.get(row.employee_id).set(row.d, Number(row.ist_minutes));
    }

    // Approved leave, one entry per day, with what kind it was — the kind is
    // what decides whether it costs anything.
    const leave = new Map();
    for (const row of (await queryClient.query(
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

    return { first, last, global, perEmployee, holidays, present, leave,
             firstLogin, shiftFor, latePolicy };
}

/**
 * POST /api/admin/payroll/generate — build (or rebuild) a month as a draft.
 *
 * Rebuilding a DRAFT is allowed and expected: somebody corrects an
 * attendance row or approves a late leave request and generates again. A
 * FINALIZED month is refused — that is what finalising means.
 */
async function generateDraft(first, actor, txClient = null) {
        const queryClient = txClient || pool;
        const existing = await queryClient.query(
            `SELECT id, status FROM payroll_runs WHERE month = $1::date`, [first]);
        if (existing.rowCount && existing.rows[0].status === "FINALIZED") {
            const error = new Error(
                "That month is finalised. Add an adjustment instead of regenerating it.");
            error.status = 409;
            throw error;
        }

        const month = await gatherMonth(first, queryClient);
        const people = (await queryClient.query(
            `SELECT e.employee_id, COALESCE(e.full_name, e.username) AS name,
                    ${istDate("e.created_at")}::text AS joined_on,
                    s.gross_monthly, s.overtime_hourly, s.salary_id,
                    s.epf_enabled, s.esi_enabled, s.pt_enabled
               FROM employees e
               LEFT JOIN LATERAL (
                   SELECT id AS salary_id, gross_monthly, overtime_hourly,
                          epf_enabled, esi_enabled, pt_enabled
                     FROM employee_salaries
                    WHERE employee_id = e.employee_id
                      AND effective_from <= $1::date
                    ORDER BY effective_from DESC LIMIT 1
               ) s ON TRUE
              WHERE e.role <> 'super_admin'
              ORDER BY e.employee_id`, [month.last])).rows;

        // What was already entered by hand against this month, so a rebuild
        // carries it forward. Read before the transaction because it is a
        // question about the run as it stands, not part of writing the new one.
        const enteredDeductions = new Map();
        const enteredOvertime = new Map();
        if (existing.rowCount) {
            for (const row of (await queryClient.query(
                // NOT automatic ones. Those are derived from the salary and
                // the rates, and are rebuilt below like the lines are. Adding
                // them to this total as well would deduct them twice.
                `SELECT employee_id, COALESCE(SUM(amount), 0) AS total
                   FROM payroll_deductions
                  WHERE run_id = $1 AND automatic = FALSE
                  GROUP BY employee_id`, [existing.rows[0].id])).rows) {
                enteredDeductions.set(row.employee_id, Number(row.total));
            }
            // Overtime hours are an approved manual entry on the line itself.
            // A salary-triggered rebuild must retain those hours while using
            // the newly saved rate, just as it retains manual deductions.
            for (const row of (await queryClient.query(
                `SELECT employee_id, overtime_hours FROM payroll_lines WHERE run_id = $1`,
                [existing.rows[0].id])).rows) {
                enteredOvertime.set(row.employee_id, Number(row.overtime_hours) || 0);
            }
        }

        // ── the statutory rates, and the parts of each salary ───────────
        //
        // Read ONCE for the run rather than per person: the rates are one row
        // of settings, and the components are one query for everybody on it.
        const rateRows = new Map((await queryClient.query(
            `SELECT key, value FROM app_settings
              WHERE key IN ('epf_rate','epf_wage_ceiling','esi_rate',
                            'esi_wage_ceiling','professional_tax')`
        )).rows.map((r) => [r.key, r.value]));
        const rates = {
            epfRate: Number(rateRows.get("epf_rate") || 0),
            epfCeiling: Number(rateRows.get("epf_wage_ceiling") || 0),
            esiRate: Number(rateRows.get("esi_rate") || 0),
            esiCeiling: Number(rateRows.get("esi_wage_ceiling") || 0),
            professionalTax: Number(rateRows.get("professional_tax") || 0),
        };

        // Basic and DA per salary version — provident fund is a share of those
        // two, not of the whole gross.
        const wageParts = new Map();
        // The whole split as well, in order, to be COPIED onto each line. The
        // migration explains why it is copied rather than referenced: an
        // in-place salary correction rewrites salary_components under the same
        // id, and a frozen payslip must not pick that up.
        const splitFor = new Map();
        for (const row of (await queryClient.query(
            `SELECT salary_id, name, rule, value, monthly, position
               FROM salary_components
              WHERE salary_id = ANY($1)
              ORDER BY salary_id, position, id`,
            [people.map((person) => person.salary_id).filter(Boolean)])).rows) {
            if (!wageParts.has(row.salary_id)) wageParts.set(row.salary_id, {});
            wageParts.get(row.salary_id)[row.name] = Number(row.monthly);
            if (!splitFor.has(row.salary_id)) splitFor.set(row.salary_id, []);
            splitFor.get(row.salary_id).push(row);
        }

        const client = txClient || await pool.connect();
        let runId;
        try {
            if (!txClient) await client.query("BEGIN");

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
                      WHERE id = $1`, [runId, globalWorking, actor]);
                // The lines are rebuilt; the adjustments are NOT. Somebody
                // entered those by hand with a reason, and regenerating the
                // month is not a reason to throw them away.
                await client.query(`DELETE FROM payroll_lines WHERE run_id = $1`, [runId]);
                // The COMPUTED deductions are rebuilt too — they are derived
                // from the salary and the rates, exactly like a line. The
                // hand-entered ones are left alone; that is what the automatic
                // flag is for, and without it a regenerated month would either
                // lose somebody's typed deduction or deduct provident fund
                // twice, silently.
                await client.query(
                    `DELETE FROM payroll_deductions
                      WHERE run_id = $1 AND automatic = TRUE`, [runId]);
            } else {
                runId = (await client.query(
                    `INSERT INTO payroll_runs (month, working_days, generated_by)
                     VALUES ($1::date, $2, $3) RETURNING id`,
                    [first, globalWorking, actor])).rows[0].id;
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

                const shift = month.shiftFor(person.employee_id);
                const theirLogins = month.firstLogin.get(person.employee_id) || new Map();

                let present = 0, paidLeave = 0, unpaidLeave = 0, absent = 0;
                let lateDays = 0, lateMinutes = 0;
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

                    // LATENESS IS DECIDED BY ATTENDANCE'S OWN RULE, called
                    // here rather than reimplemented. It already knows that a
                    // day off is never late, that an overnight shift is
                    // measured from its own start, that a sign-in after the
                    // shift ended is not lateness, and that the grace period
                    // counts. Writing "login > shift_start" here instead would
                    // mark every night-shift employee late every single day.
                    //
                    // These are days already established as working days for
                    // this person, so isDayOff is false by construction.
                    const loginMinutes = theirLogins.get(day);
                    if (loginMinutes !== undefined) {
                        const verdict = classifyLogin({
                            loginMinutes,
                            shiftStart: shift.shiftStart,
                            shiftEnd: shift.shiftEnd,
                            graceMinutes: shift.grace,
                            isDayOff: false,
                        });
                        if (verdict.status === "late") {
                            lateDays += 1;
                            lateMinutes += verdict.late_minutes;
                        }
                    }
                }

                // ── what the law takes, for whoever is enrolled ─────────
                //
                // COMPUTED HERE, WRITTEN DOWN, AND FROZEN with the rest of the
                // line. A rate changed in October must not alter August's
                // payslip, which is the same rule every other component on
                // this page follows.
                const parts = wageParts.get(person.salary_id) || {};
                const statutory = statutoryFor({
                    basic: parts["Basic"] || 0,
                    da: parts["DA"] || 0,
                    gross: Number(person.gross_monthly) || 0,
                    rates,
                    enabled: {
                        epf: person.epf_enabled,
                        esi: person.esi_enabled,
                        pt: person.pt_enabled,
                    },
                });

                for (const [kind, amount, why] of [
                    ["PF", statutory.epf, `Provident fund at ${rates.epfRate}%`],
                    ["ESI", statutory.esi, `ESI at ${rates.esiRate}%`],
                    ["PROFESSIONAL_TAX", statutory.professional_tax,
                     "Professional tax"],
                ]) {
                    // A zero is not a deduction. Writing a row for it would
                    // put "PF ₹0.00" on the payslip of somebody who is not
                    // enrolled, and count them in the Benefits card.
                    if (!(amount > 0)) continue;
                    await client.query(
                        `INSERT INTO payroll_deductions
                             (run_id, employee_id, kind, amount, reason,
                              created_by, automatic)
                         VALUES ($1,$2,$3,$4,$5,$6,TRUE)`,
                        [runId, person.employee_id, kind, amount, why, actor]);
                }

                const line = calculateLine({
                    gross: Number(person.gross_monthly) || 0,
                    workingDays: workingDays.length,
                    presentDays: present,
                    paidLeaveDays: paidLeave,
                    unpaidLeaveDays: unpaidLeave,
                    absentDays: absent,
                    lateDays,
                    lateMinutes,
                    latePolicy: month.latePolicy,
                    // Entered by hand and NOT rebuilt — the same standing the
                    // adjustments have. Somebody typed a provident fund figure
                    // with a reason against it; regenerating the month because
                    // an attendance row was corrected is not a reason to throw
                    // it away.
                    // Both kinds together: what somebody typed, and what the
                    // rates produced. The line stores the total; the rows
                    // beside it say which was which.
                    otherDeductions: money(
                        (enteredDeductions.get(person.employee_id) || 0)
                        + statutory.total),
                    // Overtime is an approved manual entry. Retain its hours
                    // across a rebuild, but apply the salary rate now in
                    // force for this draft.
                    overtimeHours: enteredOvertime.get(person.employee_id) || 0,
                    overtimeRate: Number(person.overtime_hourly) || 0,
                });

                const inserted = await client.query(
                    // salary_id IS FROZEN WITH THE REST OF THE LINE. It is
                    // which arrangement this month's gross was divided by, and
                    // it is written now because looking it up later would find
                    // whatever is in effect THEN — so a March payslip would be
                    // redrawn with April's components and its parts would stop
                    // adding up to the total above them.
                    `INSERT INTO payroll_lines
                         (run_id, employee_id, gross_monthly, working_days,
                          present_days, paid_leave_days, unpaid_leave_days,
                          absent_days, per_day, absent_deduction,
                          unpaid_deduction, late_days, late_minutes,
                          late_deduction, other_deductions,
                          overtime_hours, overtime_rate,
                          overtime_amount, net_before_adjustments, salary_id)
                     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
                             $15,$16,$17,$18,$19,$20)
                     RETURNING id`,
                    [runId, person.employee_id, line.gross, line.working_days,
                     line.present_days, line.paid_leave_days, line.unpaid_leave_days,
                     line.absent_days, line.per_day, line.absent_deduction,
                     line.unpaid_deduction, line.late_days, line.late_minutes,
                     line.late_deduction, line.other_deductions,
                     line.overtime_hours, line.overtime_rate, line.overtime_amount,
                     line.net_before_adjustments, person.salary_id || null]);

                // THE SPLIT, COPIED ONTO THE LINE. Nothing that happens to the
                // salary afterwards can reach it — which is the whole point,
                // because correcting a salary in place rewrites its components
                // under the same id.
                const split = splitFor.get(person.salary_id) || [];
                for (const [index, part] of split.entries()) {
                    await client.query(
                        `INSERT INTO payroll_line_components
                             (line_id, position, name, rule, value, monthly)
                         VALUES ($1,$2,$3,$4,$5,$6)`,
                        [inserted.rows[0].id, index, part.name, part.rule,
                         part.value, part.monthly]);
                }
            }

            if (!txClient) await client.query("COMMIT");
        } catch (error) {
            if (!txClient) await client.query("ROLLBACK").catch(() => {});
            throw error;
        } finally {
            if (!txClient) client.release();
        }

        return runId;
}

exports.generate = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const first = monthStart(req.body?.month);
    if (!first) return fail(res, 400, "Month must be YYYY-MM, e.g. 2026-08.");

    try {
        const runId = await generateDraft(first, me(req));
        await writeAudit(me(req), `PAYROLL GENERATED : ${req.body.month} (draft)`);
        return res.json({ success: true, run_id: runId, month: req.body.month });
    } catch (error) {
        if (error.status === 409) return fail(res, 409, error.message);
        return serverError(res, req, error);
    }
};

/**
 * A stored line's net, recomputed from the components already frozen on it.
 *
 * NOT calculateLine(). That one works out what the components SHOULD be from
 * the month's attendance and the policy of the moment — which is right when
 * generating a run and wrong afterwards. Editing overtime in a draft must move
 * the overtime and nothing else: the lateness charge, the provident fund, the
 * absence already established stay exactly as they were written down.
 *
 * Passing a stored line back through calculateLine without its lateness and
 * its entered deductions would silently return them to zero — somebody types
 * an overtime figure, and a professional-tax deduction quietly disappears from
 * the payslip. The arithmetic here is deliberately the one line from the
 * migration's own description of the table, and nothing more.
 *
 * @param {object} line             a row of payroll_lines
 * @param {object} [changes]        components being replaced by an edit
 */
function netFromFrozen(line, changes = {}) {
    const value = (name, fallback) => Number(
        changes[name] !== undefined ? changes[name] : fallback);

    return money(
        Number(line.gross_monthly)
        - Number(line.unpaid_deduction)
        - Number(line.absent_deduction)
        - Number(line.late_deduction || 0)
        - value("otherDeductions", line.other_deductions || 0)
        + value("overtimeAmount", line.overtime_amount || 0));
}

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
                e.designation, e.department,
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

    const deductions = (await pool.query(
        `SELECT d.*, COALESCE(c.full_name, c.username) AS created_by_name
           FROM payroll_deductions d
           LEFT JOIN employees c ON c.employee_id = d.created_by
          WHERE d.run_id = $1
          ORDER BY d.created_at`, [run.id])).rows;

    // ── what each line's gross is MADE OF ───────────────────────────────
    //
    // From the salary version the line was frozen against, not from the one in
    // effect today. A line generated before that link existed has no
    // salary_id, and gets an empty list — which the payslip reports as "not
    // recorded for this month" rather than filling in from a guess.
    //
    // One query for the whole run, keyed by salary version: a run is hundreds
    // of lines and most of them share a handful of arrangements.
    const componentsBy = new Map();
    if (lines.length) {
        for (const row of (await pool.query(
            `SELECT line_id, name, rule, value, monthly
               FROM payroll_line_components
              WHERE line_id = ANY($1)
              ORDER BY line_id, position, id`,
            [lines.map((l) => l.id)])).rows) {
            if (!componentsBy.has(row.line_id)) componentsBy.set(row.line_id, []);
            componentsBy.get(row.line_id).push({
                name: row.name, rule: row.rule,
                value: Number(row.value), monthly: Number(row.monthly),
            });
        }
    }

    const byEmployee = new Map();
    for (const adjustment of adjustments) {
        if (!byEmployee.has(adjustment.employee_id)) byEmployee.set(adjustment.employee_id, []);
        byEmployee.get(adjustment.employee_id).push(adjustment);
    }

    const deductionsBy = new Map();
    for (const deduction of deductions) {
        if (!deductionsBy.has(deduction.employee_id)) deductionsBy.set(deduction.employee_id, []);
        deductionsBy.get(deduction.employee_id).push(deduction);
    }

    for (const line of lines) {
        line.components = componentsBy.get(line.id) || [];
        // The parts, totalled, so a reader can be told whether they add back
        // to the gross above them without adding them up themselves. Null
        // rather than 0 when there are no components: "nothing recorded" and
        // "recorded as nothing" are different facts and only one is true here.
        line.components_total = line.components.length
            ? money(line.components.reduce((sum, c) => sum + Number(c.monthly), 0))
            : null;

        line.deductions = deductionsBy.get(line.employee_id) || [];
        // The frozen figure on the line is what the payslip totals; this is
        // the same money itemised, so the two are asserted against each other
        // in the tests rather than assumed to agree.
        line.entered_deductions_total = money(
            line.deductions.reduce((sum, d) => sum + Number(d.amount), 0));
        line.total_deductions = money(
            Number(line.absent_deduction) + Number(line.unpaid_deduction)
            + Number(line.late_deduction) + Number(line.other_deductions));

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
            // Every kind of deduction, so the footer's total matches the
            // column above it. Leaving lateness and the entered deductions out
            // of this sum while the lines carried them is how a footer starts
            // disagreeing with the table it is under.
            deductions: money(sum.deductions + Number(l.total_deductions)),
            late_minutes: sum.late_minutes + Number(l.late_minutes || 0),
            overtime: money(sum.overtime + Number(l.overtime_amount)),
            adjustments: money(sum.adjustments + Number(l.adjustments_total)),
            net: money(sum.net + Number(l.net_pay)),
        }), { gross: 0, deductions: 0, late_minutes: 0, overtime: 0,
              adjustments: 0, net: 0 });

        // ── WHO IS NOT ON THIS RUN ──────────────────────────────────────
        //
        // A run's lines are frozen at the moment it was generated, which is
        // right and is invisible: somebody adds an employee, opens payroll,
        // counts four rows where there should be five, and has nothing to
        // tell them why. The page cannot explain it without knowing, so the
        // names come down with the run and the page says so.
        //
        // NOT added to the lines. A person who was not on the run was not
        // paid by it, and putting them in the table at zero would be a
        // payslip nobody generated.
        const missing = (await pool.query(
            `SELECT e.employee_id, COALESCE(e.full_name, e.username) AS name
               FROM employees e
              WHERE e.role <> 'super_admin'
                AND NOT EXISTS (SELECT 1 FROM payroll_lines l
                                 WHERE l.run_id = $1
                                   AND l.employee_id = e.employee_id)
              ORDER BY e.employee_id`, [found.run.id])).rows;

        // ── A ZERO THAT EXPLAINS ITSELF ─────────────────────────────────
        //
        // Reported, and fairly: somebody set a new employee's salary, opened
        // the month, and the row said ₹0.00 while the Set salary page two
        // clicks away said ₹25,000. Both were right. A month is paid on the
        // salary in force DURING it, and that salary starts on the first of
        // the next one — but nothing on the screen said so, and a figure that
        // contradicts another screen without explaining itself is read as a
        // broken figure. ("rajesh ka salary set h already kya h bhai har ek
        // cheez ka mapping dekh na.")
        //
        // So a line worth nothing carries the date its pay begins, or null
        // when there is no salary on record at all. Two different situations
        // with two different answers — set a salary, or wait for the month it
        // starts in — and the page can now tell them apart.
        const zeroes = found.lines.filter(
            (line) => Number(line.gross_monthly) === 0);
        if (zeroes.length) {
            const starts = new Map((await pool.query(
                `SELECT employee_id, MIN(effective_from)::text AS starts_on
                   FROM employee_salaries
                  WHERE employee_id = ANY($1::varchar[])
                  GROUP BY employee_id`,
                [zeroes.map((line) => line.employee_id)])).rows
                .map((row) => [row.employee_id, row.starts_on]));
            for (const line of zeroes) {
                line.salary_starts_on = starts.get(line.employee_id) || null;
            }
        }

        return res.json({ success: true, ...found, totals,
                          missing_employees: missing });
    } catch (error) {
        return serverError(res, req, error);
    }
};

/** GET /api/admin/payroll — which months exist, and what they came to. */
exports.listRuns = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    try {
        const rows = await pool.query(
            // THE COMPONENTS, NOT JUST THE TOTAL. A chart of what payroll cost
            // over a year is read as an answer, and a bar that is only a
            // single number cannot be broken down when somebody asks why one
            // month is taller. These are the same frozen figures the payslips
            // carry, summed — not recomputed.
            `SELECT TO_CHAR(r.month,'YYYY-MM') AS month, r.status, r.working_days,
                    r.generated_at, r.finalized_at,
                    COUNT(l.id)::int AS employees,
                    COALESCE(SUM(l.gross_monthly), 0)   AS gross,
                    COALESCE(SUM(l.overtime_amount), 0) AS overtime,
                    COALESCE(SUM(l.absent_deduction + l.unpaid_deduction
                                 + l.late_deduction + l.other_deductions), 0)
                                                        AS deductions,
                    COALESCE(SUM(l.late_minutes), 0)::int AS late_minutes,
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
            // WHAT THE MONTH COST THE COMPANY, which is not the same as what
            // was paid out: the deductions were part of the cost and went
            // somewhere else. net + deductions == gross + overtime exactly,
            // which is what lets a stacked bar be drawn without a rounding
            // gap at the top.
            row.payroll_cost = money(Number(row.gross) + Number(row.overtime));
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

        const overtimeAmount = money(value * Number(line.overtime_rate));
        const net = netFromFrozen(line, { overtimeAmount });

        await pool.query(
            `UPDATE payroll_lines
                SET overtime_hours = $3, overtime_amount = $4,
                    net_before_adjustments = $5
              WHERE run_id = $1 AND employee_id = $2`,
            [run.id, employee_id, value, overtimeAmount, net]);

        await writeAudit(employee_id,
            `PAYROLL OVERTIME : ${value} hours for ${req.params.month} by ${me(req)}`);
        return res.json({
            success: true,
            line: { ...line, overtime_hours: value,
                    overtime_amount: overtimeAmount, net_before_adjustments: net },
        });
    } catch (error) {
        return serverError(res, req, error);
    }
};

// ───────────────────────────────────────────────────────────  deductions

/**
 * The deductions somebody enters by hand: provident fund, professional tax,
 * ESI, a manual line. Never computed — see the migration for why a wrong
 * statutory deduction is a legal problem rather than a bug.
 *
 * DRAFT ONLY, unlike adjustments. An adjustment is how a FINALISED month is
 * corrected, and it is added beside the payslip so both remain visible. A
 * deduction is part of the payslip itself, so adding one after finalisation
 * would rewrite what somebody was already told they were paid.
 */
const DEDUCTION_KINDS = ["MANUAL", "PROFESSIONAL_TAX", "PF", "ESI", "TAX", "OTHER"];

/** Re-freeze one line's entered-deduction total and its net. */
async function refreezeDeductions(client, runId, employeeId) {
    const line = (await client.query(
        `SELECT * FROM payroll_lines WHERE run_id = $1 AND employee_id = $2`,
        [runId, employeeId])).rows[0];
    if (!line) return null;

    const total = money(Number((await client.query(
        `SELECT COALESCE(SUM(amount), 0) AS total FROM payroll_deductions
          WHERE run_id = $1 AND employee_id = $2`, [runId, employeeId])).rows[0].total));

    const net = netFromFrozen(line, { otherDeductions: total });
    await client.query(
        `UPDATE payroll_lines SET other_deductions = $3, net_before_adjustments = $4
          WHERE run_id = $1 AND employee_id = $2`, [runId, employeeId, total, net]);
    return { other_deductions: total, net_before_adjustments: net };
}

/** POST /api/admin/payroll/:month/deductions */
exports.addDeduction = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const first = monthStart(req.params.month);
    if (!first) return fail(res, 400, "Month must be YYYY-MM.");

    const { employee_id, kind, amount, reason } = req.body || {};
    if (!employee_id) return fail(res, 400, "Which employee?");
    if (!DEDUCTION_KINDS.includes(String(kind))) {
        return fail(res, 400, `Kind must be one of ${DEDUCTION_KINDS.join(", ")}.`);
    }
    const value = Number(amount);
    if (!Number.isFinite(value) || value <= 0) {
        // A deduction that adds to somebody's pay is an adjustment. Keeping
        // the two apart is what lets "how much did we deduct this year" be
        // answered by adding a column rather than by reading reasons.
        return fail(res, 400,
            "A deduction must be a positive amount — use an adjustment to add money.");
    }
    if (value > 100000000) {
        return fail(res, 400, "That is more than ten crore — check the figure.");
    }
    if (!String(reason || "").trim()) {
        return fail(res, 400, "Every deduction needs a reason — it is what is asked about later.");
    }

    const client = await pool.connect();
    try {
        await client.query("BEGIN");
        const run = (await client.query(
            `SELECT id, status FROM payroll_runs WHERE month = $1::date FOR UPDATE`,
            [first])).rows[0];
        if (!run) {
            await client.query("ROLLBACK");
            return fail(res, 404, "That month has not been generated yet.");
        }
        if (run.status === "FINALIZED") {
            await client.query("ROLLBACK");
            return fail(res, 409,
                "That month is finalised. Add an adjustment instead — a deduction "
                + "would rewrite a payslip somebody has already been given.");
        }

        const line = await client.query(
            `SELECT 1 FROM payroll_lines WHERE run_id = $1 AND employee_id = $2`,
            [run.id, employee_id]);
        if (line.rowCount === 0) {
            await client.query("ROLLBACK");
            return fail(res, 400, "That employee is not in that month's payroll.");
        }

        const row = (await client.query(
            `INSERT INTO payroll_deductions
                 (run_id, employee_id, kind, amount, reason, created_by)
             VALUES ($1,$2,$3,$4,$5,$6)
             RETURNING id, employee_id, kind, amount, reason, created_at`,
            [run.id, employee_id, kind, money(value), String(reason).trim(), me(req)]
        )).rows[0];

        // In the SAME transaction as the insert. A deduction recorded while
        // the line still shows the old net is a payslip whose parts do not add
        // up to its total, and that is the first thing anybody checking it
        // notices.
        const frozen = await refreezeDeductions(client, run.id, employee_id);
        await client.query("COMMIT");

        await writeAudit(employee_id,
            `PAYROLL DEDUCTION : ${kind} ${money(value)} for ${req.params.month} `
            + `by ${me(req)} — ${String(reason).trim().slice(0, 80)}`);
        return res.status(201).json({ success: true, deduction: row, line: frozen });
    } catch (error) {
        await client.query("ROLLBACK").catch(() => {});
        return serverError(res, req, error);
    } finally {
        client.release();
    }
};

/** DELETE /api/admin/payroll/deductions/:id — only while the month is a draft. */
exports.removeDeduction = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) return fail(res, 400, "Which deduction?");

    const client = await pool.connect();
    try {
        await client.query("BEGIN");
        const found = (await client.query(
            `SELECT d.id, d.run_id, d.employee_id, d.kind, d.amount, r.status,
                    TO_CHAR(r.month,'YYYY-MM') AS month
               FROM payroll_deductions d
               JOIN payroll_runs r ON r.id = d.run_id
              WHERE d.id = $1`, [id])).rows[0];
        if (!found) {
            await client.query("ROLLBACK");
            return fail(res, 404, "No such deduction.");
        }
        if (found.status === "FINALIZED") {
            await client.query("ROLLBACK");
            return fail(res, 409,
                "That month is finalised. Add an adjustment the other way rather "
                + "than removing this — both stay on the record.");
        }

        await client.query(`DELETE FROM payroll_deductions WHERE id = $1`, [id]);
        const frozen = await refreezeDeductions(client, found.run_id, found.employee_id);
        await client.query("COMMIT");

        await writeAudit(found.employee_id,
            `PAYROLL DEDUCTION REMOVED : ${found.kind} ${found.amount} `
            + `for ${found.month} by ${me(req)}`);
        return res.json({ success: true, line: frozen });
    } catch (error) {
        await client.query("ROLLBACK").catch(() => {});
        return serverError(res, req, error);
    } finally {
        client.release();
    }
};

/**
 * DELETE /api/admin/payroll/:month — throw a draft away and start again.
 *
 * A DRAFT ONLY, and the cascade takes its lines, adjustments and deductions
 * with it. That is the point: somebody generated the wrong month, or wants to
 * begin from nothing rather than regenerate over the top.
 *
 * A finalised month cannot be deleted at all. It is the record of what people
 * were paid, and "delete and regenerate" is exactly the operation finalising
 * exists to prevent.
 */
exports.deleteRun = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const first = monthStart(req.params.month);
    if (!first) return fail(res, 400, "Month must be YYYY-MM.");

    try {
        const run = (await pool.query(
            `SELECT id, status FROM payroll_runs WHERE month = $1::date`, [first])).rows[0];
        if (!run) return fail(res, 404, "That month has not been generated yet.");
        if (run.status === "FINALIZED") {
            return fail(res, 409,
                "That month is finalised and cannot be deleted. It is the record of "
                + "what people were paid.");
        }

        // Counted BEFORE the delete, so the audit line says what was actually
        // thrown away rather than "a draft".
        const counts = (await pool.query(
            `SELECT (SELECT COUNT(*)::int FROM payroll_lines WHERE run_id = $1) AS lines,
                    (SELECT COUNT(*)::int FROM payroll_adjustments WHERE run_id = $1) AS adjustments,
                    (SELECT COUNT(*)::int FROM payroll_deductions WHERE run_id = $1) AS deductions`,
            [run.id])).rows[0];

        await pool.query(`DELETE FROM payroll_runs WHERE id = $1`, [run.id]);

        await writeAudit(me(req),
            `PAYROLL DELETED : ${req.params.month} draft — ${counts.lines} employees, `
            + `${counts.adjustments} adjustments, ${counts.deductions} deductions`);
        return res.json({ success: true, month: req.params.month, removed: counts });
    } catch (error) {
        return serverError(res, req, error);
    }
};

// ──────────────────────────────────────────────────────────────  reports

/**
 * GET /api/admin/payroll/benefits?span=this|previous|month
 *
 * What the company has deducted under each statutory head over a period —
 * provident fund, ESI, professional tax, tax. The three figures a finance
 * person is asked for first, and the ones that have to be remitted.
 *
 * SUMMED FROM WHAT WAS ACTUALLY DEDUCTED, not from what the rates would
 * produce. These come out of payroll_deductions, which is what somebody
 * entered against a payslip — so this reports money that really left, and a
 * head nobody has deducted under reads as nothing rather than as a projection.
 *
 * The span is the Indian financial year, April to March, like the chart.
 */
exports.benefits = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");

    const span = String(req.query.span || "this");
    try {
        const bounds = (await pool.query(
            `SELECT (NOW() AT TIME ZONE 'Asia/Kolkata')::date AS today`)).rows[0];
        const today = new Date(`${String(bounds.today).slice(0, 10)}T00:00:00Z`);
        const month = today.getUTCMonth() + 1;
        let year = month >= 4 ? today.getUTCFullYear() : today.getUTCFullYear() - 1;

        let first;
        let last;
        if (span === "month") {
            const previous = new Date(Date.UTC(today.getUTCFullYear(), month - 2, 1));
            first = previous.toISOString().slice(0, 10);
            last = first;
        } else {
            if (span === "previous") year -= 1;
            first = `${year}-04-01`;
            last = `${year + 1}-03-01`;
        }

        const rows = (await pool.query(
            `SELECT d.kind, COALESCE(SUM(d.amount), 0) AS total,
                    COUNT(DISTINCT d.employee_id)::int AS employees
               FROM payroll_deductions d
               JOIN payroll_runs r ON r.id = d.run_id
              WHERE r.month BETWEEN $1::date AND $2::date
              GROUP BY d.kind`, [first, last])).rows;

        const byKind = new Map(rows.map((r) => [r.kind, r]));
        const head = (kind) => ({
            total: money(Number(byKind.get(kind)?.total || 0)),
            employees: Number(byKind.get(kind)?.employees || 0),
            // NULL, not zero. "Nothing was deducted" and "this is not set up"
            // look identical as 0.00, and the reference shows a dash for the
            // second — so the caller is told which it is.
            configured: byKind.has(kind),
        });

        return res.json({
            success: true, span, from: first, to: last,
            epf: head("PF"),
            esi: head("ESI"),
            professional_tax: head("PROFESSIONAL_TAX"),
            tax: head("TAX"),
        });
    } catch (error) {
        return serverError(res, req, error);
    }
};

/**
 * GET /api/admin/payroll/employee/:employee_id — one person, every month.
 *
 * The payroll screen answers "what does this month cost". This answers the
 * other question, the one asked about a person rather than a month: what has
 * this employee actually been paid, and how did each of those months differ.
 *
 * EVERY FIGURE IS THE FROZEN ONE. Nothing here recomputes a past month from
 * today's attendance or today's salary — that is the whole reason the lines
 * are written down at generation time. A rise in July must leave June's
 * payslip reading exactly what June's payslip read.
 */
exports.employeeHistory = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const employeeId = String(req.params.employee_id || "").trim();
    if (!employeeId) return fail(res, 400, "Which employee?");

    try {
        const person = (await pool.query(
            `SELECT e.employee_id, COALESCE(e.full_name, e.username) AS employee_name,
                    e.designation, e.department, e.role, e.email,
                    ${istDate("e.created_at")}::text AS joined_on
               FROM employees e WHERE e.employee_id = $1`, [employeeId])).rows[0];
        if (!person) return fail(res, 404, "No such employee.");

        // Oldest first — a history reads forwards, and so does the chart
        // drawn beside it.
        const months = (await pool.query(
            `SELECT TO_CHAR(r.month,'YYYY-MM') AS month, r.status,
                    r.finalized_at, l.*,
                    COALESCE((SELECT SUM(amount) FROM payroll_adjustments a
                               WHERE a.run_id = r.id AND a.employee_id = l.employee_id),
                             0) AS adjustments_total
               FROM payroll_lines l
               JOIN payroll_runs r ON r.id = l.run_id
              WHERE l.employee_id = $1
              ORDER BY r.month`, [employeeId])).rows;

        for (const month of months) {
            month.total_deductions = money(
                Number(month.absent_deduction) + Number(month.unpaid_deduction)
                + Number(month.late_deduction || 0)
                + Number(month.other_deductions || 0));
            month.net_pay = money(Number(month.net_before_adjustments)
                                + Number(month.adjustments_total));
        }

        const salaries = (await pool.query(
            `SELECT s.id, s.gross_monthly, s.overtime_hourly,
                    s.effective_from::text, s.created_at, s.remarks,
                    s.ctc_annual, s.epf_enabled, s.esi_enabled, s.pt_enabled,
                    COALESCE(c.full_name, c.username) AS created_by_name,
                    COALESCE((
                        SELECT json_agg(json_build_object(
                                   'name', k.name, 'rule', k.rule,
                                   'value', k.value, 'monthly', k.monthly)
                                   ORDER BY k.position)
                          FROM salary_components k WHERE k.salary_id = s.id
                    ), '[]'::json) AS components
               FROM employee_salaries s
               LEFT JOIN employees c ON c.employee_id = s.created_by
              WHERE s.employee_id = $1
              ORDER BY s.effective_from DESC`, [employeeId])).rows;

        // WHAT HAS ACTUALLY BEEN PAID is only the finalised months. A draft
        // can still move, and folding one into a lifetime total would make
        // that total change every time somebody regenerates a month.
        const paid = months.filter((m) => m.status === "FINALIZED");
        const totals = {
            months_paid: paid.length,
            months_drafted: months.length - paid.length,
            paid_total: money(paid.reduce((sum, m) => sum + Number(m.net_pay), 0)),
            overtime_total: money(months.reduce(
                (sum, m) => sum + Number(m.overtime_amount), 0)),
            deductions_total: money(months.reduce(
                (sum, m) => sum + Number(m.total_deductions), 0)),
            late_minutes_total: months.reduce(
                (sum, m) => sum + Number(m.late_minutes || 0), 0),
            average_net: paid.length
                ? money(paid.reduce((sum, m) => sum + Number(m.net_pay), 0) / paid.length)
                : 0,
        };

        return res.json({ success: true, employee: person, months, salaries, totals });
    } catch (error) {
        return serverError(res, req, error);
    }
};

/**
 * GET /api/admin/payroll/:month/report?group=employee|department
 *
 * The same month the payroll screen shows, arranged for reading rather than
 * editing. `format=csv` returns the file; anything else returns JSON, so the
 * panel and a spreadsheet ask the same question of the same endpoint and
 * cannot drift apart.
 *
 * PDF is not here. The rows below carry everything a payslip prints, so
 * generating one is a rendering job — but a PDF built by guessing at a layout
 * is worse than a CSV somebody can open, and no layout has been agreed.
 */
exports.report = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const first = monthStart(req.params.month);
    if (!first) return fail(res, 400, "Month must be YYYY-MM.");

    const group = String(req.query.group || "employee").toLowerCase();
    if (!["employee", "department"].includes(group)) {
        return fail(res, 400, "group must be employee or department.");
    }

    try {
        const found = await loadRun(first);
        if (!found) return fail(res, 404, "That month has not been generated yet.");

        const rows = found.lines.map((line) => ({
            employee_id: line.employee_id,
            employee_name: line.employee_name,
            department: line.department || line.designation || "Unassigned",
            gross: money(Number(line.gross_monthly)),
            working_days: Number(line.working_days),
            present_days: Number(line.present_days),
            paid_leave_days: Number(line.paid_leave_days),
            unpaid_leave_days: Number(line.unpaid_leave_days),
            absent_days: Number(line.absent_days),
            late_days: Number(line.late_days || 0),
            late_minutes: Number(line.late_minutes || 0),
            overtime_hours: Number(line.overtime_hours),
            overtime_amount: money(Number(line.overtime_amount)),
            deductions: money(Number(line.total_deductions)),
            adjustments: money(Number(line.adjustments_total)),
            net_pay: money(Number(line.net_pay)),
        }));

        let data = rows;
        if (group === "department") {
            const byDepartment = new Map();
            for (const row of rows) {
                const key = row.department;
                if (!byDepartment.has(key)) {
                    byDepartment.set(key, {
                        department: key, employees: 0, gross: 0, overtime_amount: 0,
                        deductions: 0, adjustments: 0, net_pay: 0, late_minutes: 0,
                    });
                }
                const bucket = byDepartment.get(key);
                bucket.employees += 1;
                bucket.gross = money(bucket.gross + row.gross);
                bucket.overtime_amount = money(bucket.overtime_amount + row.overtime_amount);
                bucket.deductions = money(bucket.deductions + row.deductions);
                bucket.adjustments = money(bucket.adjustments + row.adjustments);
                bucket.net_pay = money(bucket.net_pay + row.net_pay);
                bucket.late_minutes += row.late_minutes;
            }
            data = [...byDepartment.values()]
                .sort((a, b) => a.department.localeCompare(b.department));
        }

        if (String(req.query.format || "").toLowerCase() !== "csv") {
            return res.json({
                success: true, month: req.params.month, group,
                status: found.run.status, data,
            });
        }

        const headers = Object.keys(data[0] || { employee_id: "" });
        // EVERY FIELD QUOTED, and quotes doubled inside. A name with a comma
        // in it silently shifts every column after it, and the person reading
        // the spreadsheet has no way to tell that from bad data.
        const escape = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
        const csv = [headers.map(escape).join(",")]
            .concat(data.map((row) => headers.map((h) => escape(row[h])).join(",")))
            .join("\r\n");

        res.setHeader("Content-Type", "text/csv; charset=utf-8");
        res.setHeader("Content-Disposition",
            `attachment; filename="payroll-${req.params.month}-${group}.csv"`);
        // A BOM, so Excel opens rupee symbols and non-ASCII names as UTF-8
        // instead of mojibake. Nothing else reads it as content.
        return res.send(`﻿${csv}`);
    } catch (error) {
        return serverError(res, req, error);
    }
};

// ──────────────────────────────────────────────────────── the employee's

/**
 * GET /api/payroll/mine/salary — what the CALLER is on, and their last payslip.
 *
 * NO EMPLOYEE ID, anywhere, deliberately. Everything here is read from the
 * token, so there is no parameter for somebody to change to a colleague's id —
 * which is the only way this endpoint could leak a salary, and the reason it
 * is a separate route rather than a field added to the shared profile
 * response that an admin can already fetch for anybody.
 *
 * A DRAFT IS NOT SHOWN. Until a month is finalised its figures can still move,
 * and somebody who reads a draft net and is then paid something else has been
 * told the wrong number by us.
 */
exports.mySalary = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");
    try {
        const salary = (await pool.query(
            `SELECT gross_monthly, overtime_hourly, effective_from::text, remarks
               FROM employee_salaries
              WHERE employee_id = $1
                AND effective_from <= (NOW() AT TIME ZONE 'Asia/Kolkata')::date
              ORDER BY effective_from DESC LIMIT 1`, [employeeId])).rows[0] || null;

        const latest = (await pool.query(
            `SELECT TO_CHAR(r.month,'YYYY-MM') AS month, r.finalized_at,
                    l.net_before_adjustments,
                    COALESCE((SELECT SUM(amount) FROM payroll_adjustments
                               WHERE run_id = r.id AND employee_id = $1), 0)
                        AS adjustments_total
               FROM payroll_lines l
               JOIN payroll_runs r ON r.id = l.run_id
              WHERE l.employee_id = $1 AND r.status = 'FINALIZED'
              ORDER BY r.month DESC LIMIT 1`, [employeeId])).rows[0] || null;

        if (latest) {
            latest.net_pay = money(Number(latest.net_before_adjustments)
                                 + Number(latest.adjustments_total));
        }

        return res.json({ success: true, salary, latest_payroll: latest });
    } catch (error) {
        return serverError(res, req, error);
    }
};

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

        // ── what the gross is made of ───────────────────────────────────
        //
        // From the salary version this line was frozen against. A month
        // generated before that link was recorded has none, and says so below
        // rather than being redrawn from whatever the salary is today.
        const componentRows = (line.components || []).map((c) => {
            const how = c.rule === "PERCENT_CTC" ? `${c.value}% of CTC`
                : c.rule === "PERCENT_BASIC" ? `${c.value}% of Basic`
                : c.rule === "BALANCE" ? "balance" : "";
            return rows(how ? `${c.name} (${how})` : c.name, c.monthly);
        }).join("");

        // ── every way the month took money off ──────────────────────────
        //
        // THIS USED TO BE TWO ROWS — absence and unpaid leave — while NET PAY
        // underneath was the frozen net, which already had the lateness
        // charge and the provident fund taken out of it. So a payslip showed
        // a net ₹1,800 lower than its own arithmetic with nothing anywhere
        // saying why, on the one document an employee is entitled to
        // understand. The entered deductions are itemised by what they are
        // for, because "₹1,800 deducted" and "₹1,800 of provident fund" are
        // answers to different questions.
        const itemised = (line.deductions || []);
        const itemisedTotal = itemised.reduce((sum, d) => sum + Number(d.amount), 0);
        // Anything on the frozen total that is not itemised — older rows, or a
        // deduction whose itemisation was lost — is still shown, as a
        // remainder. Dropping it would leave the column not adding up.
        const unitemised = money(Number(line.other_deductions) - itemisedTotal);
        const deductionRows = [
            Number(line.unpaid_deduction)
                ? rows(`Unpaid leave (${line.unpaid_leave_days} days at ${amount(line.per_day)})`,
                       line.unpaid_deduction, true) : "",
            Number(line.absent_deduction)
                ? rows(`Absent (${line.absent_days} days at ${amount(line.per_day)})`,
                       line.absent_deduction, true) : "",
            Number(line.late_deduction)
                ? rows(`Late arrival (${line.late_days} days, ${line.late_minutes} minutes)`,
                       line.late_deduction, true) : "",
            ...itemised.map((d) => rows(
                d.reason ? `${d.kind} — ${d.reason}` : String(d.kind),
                d.amount, true)),
            unitemised > 0 ? rows("Other deductions", unitemised, true) : "",
        ].join("");

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
      ${componentRows}
      ${rows(componentRows ? "Gross" : "Monthly gross", line.gross_monthly)}
      ${Number(line.overtime_hours) > 0
        ? rows(`Overtime (${line.overtime_hours} h at ${amount(line.overtime_rate)})`,
               line.overtime_amount)
        : ""}
    </table>
    ${componentRows ? "" : `
    <p style="margin:8px 0 0;font-size:11px;color:#94a3b8;">
      The breakdown of this gross was not recorded for this month.</p>`}

    ${deductionRows ? `
    <div class="section">DEDUCTIONS</div>
    <table>${deductionRows}</table>` : ""}

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

        // EVERY WAY THE MONTH LOSES MONEY, not the two it used to have.
        //
        // These totals were written when a line could only be reduced by
        // unpaid leave and absence. The payslip has since grown two more — the
        // lateness charge, and the entered deductions where provident fund,
        // ESI and professional tax land. getRun was updated for both; this was
        // not, so the summary printed a breakdown that did not reach the total
        // printed beneath it, and the same month reported two different
        // deduction figures depending on which screen asked.
        //
        // total_deductions is the line's own frozen figure, summed — not these
        // four re-added here. Adding them up separately is how the total and
        // its parts drift apart the next time a fifth kind is introduced.
        const totals = found.lines.reduce((sum, l) => ({
            employees: sum.employees + 1,
            gross: money(sum.gross + Number(l.gross_monthly)),
            unpaid_leave_deduction: money(sum.unpaid_leave_deduction
                                          + Number(l.unpaid_deduction)),
            absent_deduction: money(sum.absent_deduction + Number(l.absent_deduction)),
            late_deduction: money(sum.late_deduction + Number(l.late_deduction)),
            other_deductions: money(sum.other_deductions + Number(l.other_deductions)),
            total_deductions: money(sum.total_deductions + Number(l.total_deductions)),
            late_minutes: sum.late_minutes + Number(l.late_minutes || 0),
            overtime_hours: money(sum.overtime_hours + Number(l.overtime_hours)),
            overtime_amount: money(sum.overtime_amount + Number(l.overtime_amount)),
            adjustments: money(sum.adjustments + Number(l.adjustments_total)),
            net: money(sum.net + Number(l.net_pay)),
        }), { employees: 0, gross: 0, unpaid_leave_deduction: 0, absent_deduction: 0,
              late_deduction: 0, other_deductions: 0, total_deductions: 0,
              late_minutes: 0, overtime_hours: 0, overtime_amount: 0,
              adjustments: 0, net: 0 });

        return res.json({
            success: true,
            month: found.run.month,
            status: found.run.status,
            working_days: found.run.working_days,
            totals,
            // The reports asked for, from one pass rather than several queries
            // that could disagree. The two original keys keep their names —
            // the page has always read them.
            leave_deductions: found.lines
                .filter((l) => Number(l.unpaid_deduction) > 0)
                .map((l) => ({ employee_id: l.employee_id, name: l.employee_name,
                               days: l.unpaid_leave_days, amount: l.unpaid_deduction })),
            overtime: found.lines
                .filter((l) => Number(l.overtime_hours) > 0)
                .map((l) => ({ employee_id: l.employee_id, name: l.employee_name,
                               hours: l.overtime_hours, amount: l.overtime_amount })),
            lateness: found.lines
                .filter((l) => Number(l.late_deduction) > 0)
                .map((l) => ({ employee_id: l.employee_id, name: l.employee_name,
                               days: l.late_days, minutes: l.late_minutes,
                               amount: l.late_deduction })),
            // Itemised, because "₹1,800 deducted" and "₹1,800 of provident
            // fund" are answers to different questions, and the second is the
            // one asked when somebody queries their payslip.
            deductions: found.lines
                .filter((l) => Number(l.other_deductions) > 0)
                .map((l) => ({ employee_id: l.employee_id, name: l.employee_name,
                               amount: l.other_deductions,
                               items: (l.deductions || []).map((d) => ({
                                   kind: d.kind, amount: d.amount, reason: d.reason })) })),
        });
    } catch (error) {
        return serverError(res, req, error);
    }
};
