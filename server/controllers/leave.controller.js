/**
 * Leave: asking for it, deciding on it, and what it does to attendance.
 *
 * WHO MAY DO WHAT — the whole authorisation model of this file:
 *
 *   an employee   applies, reads their OWN requests, and cancels one that is
 *                 still pending. Nothing else. There is no employee route
 *                 here that takes somebody else's id.
 *   an admin      sees every request, approves, rejects, and revokes an
 *                 approval that turns out to be wrong.
 *
 * An employee cannot cancel an approved request, which is the owner's
 * decision and the right one: once it is approved the roster has been planned
 * around it, and "I changed my mind" is a conversation, not a button.
 * Revoking is an administrator's action and is recorded as one.
 *
 * WHAT A DAY COSTS. total_days is worked out when the request is MADE, from
 * the weekly offs and holidays in force then, and stored. It is never
 * recomputed: adding a holiday next month must not quietly rewrite a request
 * somebody has already been told is approved.
 *
 * EVERY DECISION IS WRITTEN TO THE AUDIT LOG — applied, approved, rejected,
 * cancelled, revoked, with who did it. Leave is the part of this product
 * closest to somebody's pay, and "who approved that" is asked long after
 * anybody remembers.
 */
const pool = require("../config/db");
const { istToday } = require("../utils/ist_sql");
const { isNonWorkingDay } = require("../utils/attendance_status");
const mailer = require("../utils/mailer");
// One definition of "who may act on whom", shared with admin.controller.
const { canManage } = require("../middleware/admin.middleware");

const TYPES = ["CASUAL", "SICK", "UNPAID"];
const OPEN = "PENDING";

const me = (req) => req.employee?.employee_id;
const isAdmin = (req) => ["admin", "super_admin"].includes(req.employee?.role);

const fail = (res, status, message) =>
    res.status(status).json({ success: false, message });

function serverError(res, req, error) {
    console.error("[500]", req.method, req.originalUrl, error.message);
    return res.status(500).json({ success: false, message: "Internal server error" });
}

/** YYYY-MM-DD or nothing. Anything else is a typo, not a date. */
const isIsoDate = (value) => /^\d{4}-\d{2}-\d{2}$/.test(String(value || ""));

function daysBetween(startIso, endIso) {
    const days = [];
    const start = new Date(`${startIso}T00:00:00Z`);
    const end = new Date(`${endIso}T00:00:00Z`);
    for (let d = start; d <= end; d.setUTCDate(d.getUTCDate() + 1)) {
        days.push(d.toISOString().slice(0, 10));
    }
    return days;
}

/**
 * How many days this request actually costs.
 *
 * Weekly offs and holidays inside the range are NOT counted. Somebody who
 * takes Friday to Monday over a weekend has taken two days off, and telling
 * them they used four would be wrong in the direction that matters.
 */
async function countWorkingDays(employeeId, startIso, endIso, halfDay) {
    if (halfDay) return 0.5;

    const config = (await pool.query(
        `SELECT COALESCE(c.weekly_offs, g.weekly_offs) AS weekly_offs
           FROM employees e
           LEFT JOIN employee_configs c ON c.employee_id = e.employee_id
           LEFT JOIN employee_configs g ON g.employee_id IS NULL
          WHERE e.employee_id = $1`, [employeeId])).rows[0] || {};

    const holidays = new Set((await pool.query(
        `SELECT TO_CHAR(holiday_date, 'YYYY-MM-DD') AS d FROM holidays
          WHERE holiday_date BETWEEN $1::date AND $2::date`,
        [startIso, endIso])).rows.map((r) => r.d));

    let count = 0;
    for (const day of daysBetween(startIso, endIso)) {
        if (!isNonWorkingDay(day, config.weekly_offs, holidays)) count += 1;
    }
    return count;
}

async function writeAudit(employeeId, activity) {
    await pool.query(
        `INSERT INTO activity_logs (employee_id, activity) VALUES ($1, $2)`,
        [employeeId, activity]).catch(() => {});
}

/**
 * Tell somebody, and never let the telling break the decision.
 *
 * A leave request that was approved is approved whether or not the mail
 * server was reachable. Wrapping this makes the email a courtesy rather than
 * a dependency — and a failure is written to the log, so a silent mailbox is
 * visible rather than assumed.
 */
async function notify({ to, subject, text, html, about }) {
    if (!to || !to.length) return;
    if (!mailer.isConfigured()) return;
    try {
        await mailer.send({ to: to.join(", "), subject, text, html });
    } catch (error) {
        await writeAudit(about, `LEAVE EMAIL FAILED : ${error.message}`);
    }
}

const LEAVE_MAIL = (heading, lines) => `<!doctype html>
<html><body style="margin:0;background:#f1f5f9;font-family:-apple-system,
     Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="padding:24px 12px;"><tr><td align="center">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="max-width:560px;background:#fff;border:1px solid #e2e8f0;
                  border-radius:12px;overflow:hidden;">
      <tr><td style="padding:18px 22px;background:#0f172a;color:#fff;
                     font-size:15px;font-weight:700;">${heading}</td></tr>
      <tr><td style="padding:20px 22px;font-size:14px;color:#0f172a;
                     line-height:1.6;">${lines}</td></tr>
      <tr><td style="padding:14px 22px;background:#f8fafc;color:#64748b;
                     font-size:11px;border-top:1px solid #e2e8f0;">
        Amaze Connect · leave</td></tr>
    </table>
  </td></tr></table>
</body></html>`;

const escapeHtml = (value) => String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

function describe(row) {
    const span = row.start_date === row.end_date
        ? row.start_date
        : `${row.start_date} to ${row.end_date}`;
    return `${row.leave_type} · ${span} · ${row.total_days} day`
         + (Number(row.total_days) === 1 ? "" : "s");
}

/** Every admin, for "somebody has applied". */
async function adminAddresses() {
    return (await pool.query(
        `SELECT email FROM employees
          WHERE role IN ('admin','super_admin') AND email IS NOT NULL
            AND suspended = FALSE`)).rows.map((r) => r.email).filter(Boolean);
}

// ─────────────────────────────────────────────────────────── applying

/** POST /api/leave — an employee asks for time off. */
exports.apply = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");

    const { leave_type, reason, start_date, end_date, half_day } = req.body || {};

    if (!TYPES.includes(String(leave_type))) {
        return fail(res, 400, `Leave type must be one of ${TYPES.join(", ")}.`);
    }
    if (!String(reason || "").trim()) {
        return fail(res, 400, "A reason is required — it is what the approver reads.");
    }
    if (String(reason).length > 1000) {
        return fail(res, 400, "That reason is too long — 1000 characters at most.");
    }
    if (!isIsoDate(start_date) || !isIsoDate(end_date)) {
        return fail(res, 400, "Dates must be YYYY-MM-DD.");
    }
    if (end_date < start_date) {
        return fail(res, 400, "The last day cannot be before the first.");
    }

    const isHalf = half_day === true || half_day === "true";
    if (isHalf && start_date !== end_date) {
        return fail(res, 400, "A half day is half of one day — pick a single date.");
    }

    // A YEAR IS THE LIMIT, and it is a sanity check rather than a policy. It
    // exists because a typo in the year ("2027-01-05" for "2026-01-05") is
    // otherwise a 365-day request that somebody has to notice.
    const span = daysBetween(start_date, end_date).length;
    if (span > 366) {
        return fail(res, 400, "That is more than a year — check the dates.");
    }

    try {
        const total = await countWorkingDays(employeeId, start_date, end_date, isHalf);
        if (total <= 0) {
            return fail(res, 400,
                "Every day in that range is already a weekly off or a holiday.");
        }

        // NO TWO REQUESTS FOR THE SAME DAY. Overlapping leave is how somebody
        // ends up with two approvals for one Tuesday and a payroll run that
        // deducts it twice. Cancelled and rejected ones do not count — those
        // days are free again.
        const clash = await pool.query(
            `SELECT id, start_date, end_date, status FROM leave_requests
              WHERE employee_id = $1
                AND status IN ('PENDING','APPROVED')
                AND start_date <= $3::date AND end_date >= $2::date
              LIMIT 1`,
            [employeeId, start_date, end_date]);
        if (clash.rowCount > 0) {
            const other = clash.rows[0];
            return fail(res, 409,
                `That overlaps a ${other.status.toLowerCase()} request for `
                + `${other.start_date} to ${other.end_date}.`);
        }

        const row = (await pool.query(
            `INSERT INTO leave_requests
                 (employee_id, leave_type, reason, start_date, end_date,
                  total_days, half_day)
             VALUES ($1, $2, $3, $4::date, $5::date, $6, $7)
             RETURNING id, employee_id, leave_type, reason,
                       start_date::text, end_date::text, total_days,
                       half_day, status, created_at`,
            [employeeId, leave_type, String(reason).trim(),
             start_date, end_date, total, isHalf])).rows[0];

        await writeAudit(employeeId,
            `LEAVE APPLIED : ${describe(row)} — ${String(reason).trim().slice(0, 80)}`);

        const who = (await pool.query(
            `SELECT COALESCE(full_name, username) AS name FROM employees
              WHERE employee_id = $1`, [employeeId])).rows[0]?.name || employeeId;
        await notify({
            to: await adminAddresses(),
            about: employeeId,
            subject: `Leave request — ${who} (${describe(row)})`,
            text: `${who} has asked for leave.\n\n${describe(row)}\n\n`
                + `Reason: ${String(reason).trim()}\n\n`
                + `Approve or reject it in the admin console.`,
            html: LEAVE_MAIL("Leave request",
                `<p><strong>${escapeHtml(who)}</strong> has asked for leave.</p>`
              + `<p style="margin:12px 0;padding:12px;background:#f8fafc;`
              + `border-radius:8px;"><strong>${escapeHtml(describe(row))}</strong><br>`
              + `${escapeHtml(String(reason).trim())}</p>`
              + `<p style="color:#475569;">Approve or reject it in the admin console.</p>`),
        });

        return res.status(201).json({ success: true, leave: row });
    } catch (error) {
        return serverError(res, req, error);
    }
};

// ─────────────────────────────────────────────────────────── reading

/**
 * GET /api/leave/mine — an employee's own history.
 *
 * There is no id parameter. The only request this can answer is the caller's,
 * which is the same rule the profile routes follow.
 */
exports.mine = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");
    try {
        const rows = await pool.query(
            `SELECT l.id, l.leave_type, l.reason, l.start_date::text,
                    l.end_date::text, l.total_days, l.half_day, l.status,
                    l.remarks, l.approved_at, l.created_at,
                    COALESCE(a.full_name, a.username) AS approved_by_name
               FROM leave_requests l
               LEFT JOIN employees a ON a.employee_id = l.approved_by
              WHERE l.employee_id = $1
              ORDER BY l.start_date DESC, l.id DESC
              LIMIT 200`, [employeeId]);
        return res.json({ success: true, leave: rows.rows });
    } catch (error) {
        return serverError(res, req, error);
    }
};

/** GET /api/admin/leave — every request, with search and filters. */
exports.list = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");

    const conditions = [];
    const values = [];
    const { status, employee_id, from, to, search } = req.query || {};

    if (status) {
        const wanted = String(status).toUpperCase();
        if (!["PENDING", "APPROVED", "REJECTED", "CANCELLED", "REVOKED"].includes(wanted)) {
            return fail(res, 400, "Unknown status.");
        }
        values.push(wanted);
        conditions.push(`l.status = $${values.length}`);
    }
    if (employee_id) {
        values.push(String(employee_id));
        conditions.push(`l.employee_id = $${values.length}`);
    }
    if (from) {
        if (!isIsoDate(from)) return fail(res, 400, "from must be YYYY-MM-DD.");
        values.push(from);
        conditions.push(`l.end_date >= $${values.length}::date`);
    }
    if (to) {
        if (!isIsoDate(to)) return fail(res, 400, "to must be YYYY-MM-DD.");
        values.push(to);
        conditions.push(`l.start_date <= $${values.length}::date`);
    }
    if (search) {
        // Parameterised, like every other search in this product. The value
        // never reaches the SQL text.
        values.push(`%${String(search).trim()}%`);
        conditions.push(`(e.full_name ILIKE $${values.length}`
                      + ` OR e.username ILIKE $${values.length}`
                      + ` OR l.employee_id ILIKE $${values.length}`
                      + ` OR l.reason ILIKE $${values.length})`);
    }

    const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
    const page = Math.max(1, Number(req.query.page) || 1);
    const limit = Math.min(100, Math.max(1, Number(req.query.limit) || 50));

    try {
        const rows = await pool.query(
            `SELECT l.id, l.employee_id, l.leave_type, l.reason,
                    l.start_date::text, l.end_date::text, l.total_days,
                    l.half_day, l.status, l.remarks, l.approved_at,
                    l.created_at,
                    COALESCE(e.full_name, e.username) AS employee_name,
                    COALESCE(a.full_name, a.username) AS approved_by_name
               FROM leave_requests l
               JOIN employees e ON e.employee_id = l.employee_id
               LEFT JOIN employees a ON a.employee_id = l.approved_by
               ${where}
              ORDER BY CASE WHEN l.status = 'PENDING' THEN 0 ELSE 1 END,
                       l.created_at DESC
              LIMIT ${limit} OFFSET ${(page - 1) * limit}`, values);

        const total = Number((await pool.query(
            `SELECT COUNT(*)::int AS n FROM leave_requests l
               JOIN employees e ON e.employee_id = l.employee_id ${where}`,
            values)).rows[0].n);

        const pending = Number((await pool.query(
            `SELECT COUNT(*)::int AS n FROM leave_requests WHERE status = 'PENDING'`
        )).rows[0].n);

        return res.json({ success: true, data: rows.rows, total, page, pending });
    } catch (error) {
        return serverError(res, req, error);
    }
};

// ─────────────────────────────────────────────────────────── deciding

async function loadRequest(id) {
    return (await pool.query(
        `SELECT l.*, l.start_date::text AS start_date, l.end_date::text AS end_date,
                COALESCE(e.full_name, e.username) AS employee_name, e.email,
                e.role
           FROM leave_requests l
           JOIN employees e ON e.employee_id = l.employee_id
          WHERE l.id = $1`, [id])).rows[0] || null;
}

async function decide(req, res, { to, from, verb, past }) {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) return fail(res, 400, "Which request?");

    const remarks = String(req.body?.remarks ?? "").trim();
    if (remarks.length > 1000) {
        return fail(res, 400, "That remark is too long — 1000 characters at most.");
    }
    // A REJECTION NEEDS A REASON. It is read by the person who asked, and a
    // bare "Rejected" is the thing that gets asked about in person — which is
    // the conversation this feature exists to save.
    if (to === "REJECTED" && !remarks) {
        return fail(res, 400, "Say why it is being rejected — they will read it.");
    }

    try {
        const existing = await loadRequest(id);
        if (!existing) return fail(res, 404, "No such leave request.");

        // NOBODY DECIDES THEIR OWN LEAVE.
        //
        // An administrator is an employee too — they take leave like anybody
        // else, and the apply route does not ask about roles. What it must
        // not become is a button that grants it: an approval is somebody
        // ELSE agreeing, and an approval by the person asking is not a
        // decision, it is a formality with a name on it.
        //
        // Left open, this was real: an admin applied and approved in two
        // clicks, and approved_by carried their own id.
        //
        // The super admin is not exempt. They are the owner, and the owner
        // taking leave is a thing they can simply do — but if it goes through
        // this system it is recorded as a decision, and a decision needs two
        // people.
        if (existing.employee_id === me(req)) {
            return fail(res, 403,
                "You cannot decide your own leave request — ask another "
                + "administrator.");
        }

        // AN ADMIN'S LEAVE IS THE SUPER ADMIN'S TO DECIDE.
        //
        // The owner's rule, and the same hierarchy the rest of the product
        // already enforces: an admin manages employees, and only the super
        // admin manages admins. canManage is that rule, in one place, so
        // leave cannot drift from what an admin may do everywhere else.
        //
        // The corner worth naming: with only ONE super admin, their own leave
        // has nobody to approve it — they are blocked by the self-check
        // above. That is not an oversight to work around here; it is what
        // having one owner means. A second super admin can decide the first's.
        const denial = canManage(req.employee, existing.employee_id, existing.role);
        if (denial) return fail(res, 403, denial);

        if (!from.includes(existing.status)) {
            return fail(res, 409,
                `That request is ${existing.status.toLowerCase()}, so it cannot be ${past}.`);
        }

        const row = (await pool.query(
            `UPDATE leave_requests
                SET status = $2, approved_by = $3,
                    approved_at = NOW() AT TIME ZONE 'UTC',
                    remarks = COALESCE(NULLIF($4, ''), remarks),
                    updated_at = NOW() AT TIME ZONE 'UTC'
              WHERE id = $1
              RETURNING id, employee_id, leave_type, start_date::text,
                        end_date::text, total_days, status, remarks`,
            [id, to, me(req), remarks])).rows[0];

        const actor = req.employee?.employee_id;
        await writeAudit(existing.employee_id,
            `LEAVE ${past.toUpperCase()} : ${describe(row)} by ${actor}`
            + (remarks ? ` — ${remarks.slice(0, 80)}` : ""));
        await writeAudit(actor,
            `LEAVE ${past.toUpperCase()} : ${existing.employee_name}, ${describe(row)}`);

        await notify({
            to: existing.email ? [existing.email] : [],
            about: existing.employee_id,
            subject: `Your leave was ${past} — ${describe(row)}`,
            text: `Your leave request was ${past}.\n\n${describe(row)}\n`
                + (remarks ? `\nRemarks: ${remarks}\n` : ""),
            html: LEAVE_MAIL(`Leave ${past}`,
                `<p>Your leave request was <strong>${escapeHtml(past)}</strong>.</p>`
              + `<p style="margin:12px 0;padding:12px;background:#f8fafc;`
              + `border-radius:8px;">${escapeHtml(describe(row))}</p>`
              + (remarks
                 ? `<p><strong>Remarks:</strong> ${escapeHtml(remarks)}</p>` : "")),
        });

        return res.json({ success: true, leave: row });
    } catch (error) {
        return serverError(res, req, error);
    }
}

/** POST /api/admin/leave/:id/approve */
exports.approve = (req, res) =>
    decide(req, res, { to: "APPROVED", from: [OPEN], verb: "approve", past: "approved" });

/** POST /api/admin/leave/:id/reject */
exports.reject = (req, res) =>
    decide(req, res, { to: "REJECTED", from: [OPEN], verb: "reject", past: "rejected" });

/**
 * POST /api/admin/leave/:id/revoke — undo an approval.
 *
 * An administrator's action, deliberately. Once leave is approved the roster
 * has been planned around it, so the employee cannot take it back — but a
 * mistaken approval has to be undoable by somebody.
 */
exports.revoke = (req, res) =>
    decide(req, res, { to: "REVOKED", from: ["APPROVED"], verb: "revoke", past: "revoked" });

/**
 * POST /api/leave/:id/cancel — the employee withdraws their own request.
 *
 * Only while it is still pending, and only their own. Both halves matter: one
 * is the owner's rule about approved leave, the other is the reason there is
 * no employee route in this file that takes somebody else's id.
 */
exports.cancel = async (req, res) => {
    const employeeId = me(req);
    if (!employeeId) return fail(res, 401, "Unauthenticated");
    const id = Number(req.params.id);
    if (!Number.isInteger(id)) return fail(res, 400, "Which request?");

    try {
        const existing = await loadRequest(id);
        if (!existing) return fail(res, 404, "No such leave request.");
        if (existing.employee_id !== employeeId) {
            // Not "you may not cancel this one" — that would confirm it
            // exists. The same answer as a request that is not there.
            return fail(res, 404, "No such leave request.");
        }
        if (existing.status !== OPEN) {
            return fail(res, 409, existing.status === "APPROVED"
                ? "That leave is already approved. Ask an administrator to revoke it."
                : `That request is already ${existing.status.toLowerCase()}.`);
        }

        const row = (await pool.query(
            `UPDATE leave_requests
                SET status = 'CANCELLED', updated_at = NOW() AT TIME ZONE 'UTC'
              WHERE id = $1
              RETURNING id, leave_type, start_date::text, end_date::text,
                        total_days, status`, [id])).rows[0];

        await writeAudit(employeeId, `LEAVE CANCELLED : ${describe(row)}`);
        return res.json({ success: true, leave: row });
    } catch (error) {
        return serverError(res, req, error);
    }
};

/**
 * GET /api/admin/leave/on/:date — who is on leave on a given day.
 *
 * Used by the dashboard and by anybody planning a day. Approved only: a
 * pending request is not time off yet.
 */
exports.onDate = async (req, res) => {
    if (!isAdmin(req)) return fail(res, 403, "Admins only.");
    const date = req.params.date;
    if (!isIsoDate(date)) return fail(res, 400, "Date must be YYYY-MM-DD.");
    try {
        const rows = await pool.query(
            `SELECT l.employee_id, l.leave_type, l.half_day,
                    COALESCE(e.full_name, e.username) AS employee_name
               FROM leave_requests l
               JOIN employees e ON e.employee_id = l.employee_id
              WHERE l.status = 'APPROVED'
                AND $1::date BETWEEN l.start_date AND l.end_date
              ORDER BY employee_name`, [date]);
        return res.json({ success: true, date, on_leave: rows.rows });
    } catch (error) {
        return serverError(res, req, error);
    }
};

exports.countWorkingDays = countWorkingDays;
