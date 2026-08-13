const pool = require("../config/db");
const { endSession, markLoggedIn } = require("../utils/session");
const { isOnlineSql } = require("../utils/presence");
const { istDate, istToday, isTodayIST } = require("../utils/ist_sql");
const {
    canManage,
    ROLE_SUPER_ADMIN,
    ROLE_ADMIN,
} = require("../middleware/admin.middleware");
const {
    validatePassword,
    generateTemporaryPassword,
} = require("../utils/password_policy");
const { isNonWorkingDay, shiftIsoDate } = require("../utils/attendance_status");

const VALID_ROLES = ["employee", ROLE_ADMIN, ROLE_SUPER_ADMIN];

// Company ke role limits — business rule, code me enforce.
//   super_admin : max 3   (owner-level, sab kuch kar sakte hain)
//   admin       : max 20  (employees manage karte hain)
//   employee    : unlimited
const ROLE_LIMITS = { [ROLE_SUPER_ADMIN]: 3, [ROLE_ADMIN]: 20 };

/** Kitne log is role pe hain (optional: ek employee ko chhod ke). */
async function countRole(role, excludeId = null) {
    const result = excludeId
        ? await pool.query(
            `SELECT COUNT(*) FROM employees WHERE role = $1 AND employee_id <> $2`,
            [role, excludeId])
        : await pool.query(`SELECT COUNT(*) FROM employees WHERE role = $1`, [role]);
    return Number(result.rows[0].count);
}

/**
 * Kya `actorRole` wala user `targetRole` ka account bana sakta hai?
 * Allowed ho to null, warna error message.
 *
 * RULES:
 *   super_admin -> super_admin, admin, employee  (sab kuch)
 *   admin       -> employee HI                   (admins ko admin banane ka
 *                                                 haq sirf super admin ke paas)
 */
function canCreateRole(actorRole, targetRole) {
    if (actorRole === ROLE_SUPER_ADMIN) return null;
    if (actorRole === ROLE_ADMIN && targetRole === "employee") return null;
    return targetRole === ROLE_SUPER_ADMIN
        ? "Only a super admin can create another super admin."
        : "Only a super admin can create admin accounts. Admins can create employees.";
}

/**
 * Target employee pe action allowed hai ya nahi — role hierarchy ke hisaab se.
 * Allowed ho to true, warna response bhej ke false return karta hai.
 */
async function assertCanManage(req, res, targetId) {
    if (!targetId) return true;
    const r = await pool.query(
        `SELECT role FROM employees WHERE employee_id = $1`, [targetId]
    );
    if (r.rows.length === 0) {
        res.status(404).json({ success: false, message: "Employee not found" });
        return false;
    }
    const denial = canManage(req.employee, targetId, r.rows[0].role);
    if (denial) {
        res.status(403).json({ success: false, message: denial });
        return false;
    }
    return true;
}


// ──────────────────────────────────────────────────────────────────────────────
//  FIX #3/#4: getEmployees
//  - Status: attendance-based (open session = online, else offline).
//  - FIX #5: "open session" ab sirf pichhle 16 ghante ke andar shuru hui
//    session ko online maanta hai. Pehle koi time-limit nahi thi — ek
//    crashed/force-closed app ka dangling attendance row (logout_time=NULL)
//    employee ko HAMESHA "online" dikhata rehta, chahe wo dino se offline ho.
//  - last_seen: was returning NOW() for online users (meaningless "just now" always).
//    FIX: return NULL for online users (client shows "Online" from status column),
//    and for offline users return the most recent activity_log timestamp or last logout.
// ──────────────────────────────────────────────────────────────────────────────
// ──────────────────────────────────────────────────────────────────────────────
//  SCALE FIX (1000+ employees)
//
//  Purani query har request pe SAARE employees laati thi, aur har row ke liye
//  4 correlated subqueries chalati thi (2× attendance EXISTS, 1× MAX(logout),
//  1× MAX(activity_logs.created_at), 1× config lookup). 1000 employees +
//  20 lakh activity_logs pe measure kiya gaya:
//
//      bina indexes ke : 55  second
//      indexes ke saath: 117 second
//
//  ...aur admin panel ka Employees tab ye HAR 5 SECOND maarta hai. 20 admins
//  ke saath ye database ko poori tarah bitha deta. 10,000 employees pe to
//  ye kabhi complete hi na hota.
//
//  Do cheezein badli:
//   1. PAGINATION — pehle sirf ek page ke employees select hote hain, phir
//      unhi ke liye lookups hote hain (1000 ki jagah 50 lookups).
//   2. LATERAL + "ORDER BY created_at DESC LIMIT 1" — MAX() ki jagah, taaki
//      Postgres seedha index se latest row uthaye (MAX() us index ko use
//      nahi kar paata tha).
//
//  Search bhi ab server-side hai — pehle client 1000 rows download karke
//  memory me filter karta tha.
// ──────────────────────────────────────────────────────────────────────────────
exports.getEmployees = async (req, res) => {
    try {
        const page   = Math.max(1, parseInt(req.query.page, 10) || 1);
        let   limit  = parseInt(req.query.limit, 10);
        if (!Number.isFinite(limit) || limit < 1) limit = 50;
        if (limit > 200) limit = 200;
        const offset = (page - 1) * limit;
        const search = String(req.query.search || "").trim();

        // full_name is searched too, and it is the field people actually
        // type. Chat, reports and the audit log all show somebody by their
        // NAME; only this table showed the login username. So an admin who
        // had just read a message from "Priya Nair" searched for her here and
        // was told there was no such person — the account is AD100/manager.
        // designation as well: "who are the QA people" is a real question.
        const searchWhere = search
            ? `WHERE (employee_id ILIKE $1 OR username ILIKE $1 OR role ILIKE $1
                      OR COALESCE(full_name, '') ILIKE $1
                      OR COALESCE(designation, '') ILIKE $1)`
            : "";
        const searchVals = search ? [`%${search}%`] : [];
        const p = searchVals.length;

        const result = await pool.query(`
            SELECT
                e.employee_id,
                e.username,
                e.role,
                e.full_name,
                e.designation,
                CASE WHEN oa.hit IS NOT NULL THEN 'online' ELSE 'offline' END AS status,
                CASE WHEN oa.hit IS NOT NULL THEN NULL
                     ELSE GREATEST(
                         COALESCE(la.last_logout, '1970-01-01'::timestamp),
                         COALESCE(ll.last_log,    '1970-01-01'::timestamp)
                     )
                END AS last_seen,
                COALESCE(ec.verbose_logging, false) AS verbose_logging,
                e.suspended
            FROM (
                SELECT employee_id, username, role, full_name, designation, suspended
                FROM employees
                ${searchWhere}
                ORDER BY employee_id ASC
                LIMIT $${p + 1} OFFSET $${p + 2}
            ) e
            LEFT JOIN LATERAL (
                -- Online needs a LIVE SESSION as well as an open attendance
                -- row. An open row alone only says work was started; any app
                -- that ended without a clean logout left a green dot for
                -- sixteen hours. See utils/presence.js.
                SELECT 1 AS hit WHERE ${isOnlineSql("e")}
            ) oa ON true
            LEFT JOIN LATERAL (
                SELECT a2.logout_time AS last_logout
                FROM attendance a2
                WHERE a2.employee_id = e.employee_id
                  AND a2.logout_time IS NOT NULL
                ORDER BY a2.logout_time DESC
                LIMIT 1
            ) la ON true
            LEFT JOIN LATERAL (
                SELECT al.created_at AS last_log
                FROM activity_logs al
                WHERE al.employee_id = e.employee_id
                ORDER BY al.created_at DESC
                LIMIT 1
            ) ll ON true
            LEFT JOIN employee_configs ec ON ec.employee_id = e.employee_id
            ORDER BY e.employee_id ASC
        `, [...searchVals, limit, offset]);

        const countResult = await pool.query(
            `SELECT COUNT(*) FROM employees ${searchWhere}`, searchVals
        );

        // Role counts + limits — admin panel inhe "2 / 3 super admins"
        // jaisa dikhata hai, taaki add karne se PEHLE pata chale ki jagah hai.
        const roleCounts = await pool.query(
            `SELECT role, COUNT(*)::int AS n FROM employees GROUP BY role`
        );
        const counts = Object.fromEntries(roleCounts.rows.map(r => [r.role, r.n]));

        return res.json({
            success: true,
            data: result.rows.map(row => ({
                ...row,
                last_seen: row.last_seen && new Date(row.last_seen).getFullYear() > 1970
                    ? row.last_seen
                    : null
            })),
            total: Number(countResult.rows[0].count),
            page,
            limit,
            role_counts: counts,
            role_limits: ROLE_LIMITS,
        });

    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};


exports.createEmployee = async (req, res) => {
    const {
        employee_id, username, password, role = "employee",
        full_name = null, designation = null,
    } = req.body || {};

    // BUG FIX: pehle empty/missing fields directly DB tak pahunch jaate the.
    if (!employee_id || !username || !password) {
        return res.status(400).json({
            success: false,
            message: "employee_id, username and password are required"
        });
    }

    if (!VALID_ROLES.includes(role)) {
        return res.status(400).json({
            success: false,
            message: `role must be one of: ${VALID_ROLES.join(", ")}`
        });
    }

    // Same rules as a change or a reset. Account creation used to accept any
    // non-empty string, which would have made it the way around the policy
    // the other two enforce.
    const weak = validatePassword(password, { username, employeeId: employee_id });
    if (weak) {
        return res.status(400).json({ success: false, message: weak });
    }

    // Kaun kis role ka account bana sakta hai
    const denial = canCreateRole(req.employee?.role, role);
    if (denial) {
        return res.status(403).json({ success: false, message: denial });
    }

    // Role caps — super_admin max 3, admin max 20
    if (ROLE_LIMITS[role]) {
        const current = await countRole(role);
        if (current >= ROLE_LIMITS[role]) {
            return res.status(409).json({
                success: false,
                message: `Limit reached — a maximum of ${ROLE_LIMITS[role]} `
                       + `${role === ROLE_SUPER_ADMIN ? "super admins" : "admins"} `
                       + `are allowed (currently ${current}). `
                       + `Remove one before adding another.`,
            });
        }
    }

    try {
        const bcrypt = require("bcryptjs");
        const hashedPassword = await bcrypt.hash(password, 10);

        await pool.query(
            `INSERT INTO employees (employee_id, username, password, role, full_name, designation)
             VALUES ($1, $2, $3, $4, $5, $6)`,
            [
                employee_id, username, hashedPassword, role,
                full_name || username,
                designation || (role === "super_admin" ? "Administrator"
                                : role === "admin" ? "Manager" : "Employee"),
            ]
        );

        return res.json({ success: true, message: "Employee created" });

    } catch (err) {
        // BUG FIX: duplicate employee_id/username pehle raw Postgres error
        // (500 + internal constraint message) leak karta tha. Ab clean 409.
        if (err.code === "23505") {
            return res.status(409).json({
                success: false,
                // Usernames are compared without case since login stopped
                // being case-sensitive, so "Admin" collides with "admin".
                // Saying so beats leaving the admin staring at a name that
                // looks unused.
                message: "An employee with this employee_id or username already "
                       + "exists. Usernames are not case-sensitive, so 'Admin' "
                       + "and 'admin' count as the same name."
            });
        }
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

/**
 * An admin issues a new password for someone who cannot sign in.
 *
 * There is no email on file for anybody, so there is nothing to send a reset
 * link to — the admin hands the password over in person, and the account is
 * marked `must_change_password` so that temporary password is replaced the
 * moment the employee signs in with it.
 *
 * The admin may supply the password or let the server generate a readable
 * one. Either way it is returned in the response exactly once: the column
 * only ever holds the bcrypt hash, so this is the sole opportunity to read
 * it, and there is no way to recover it afterwards.
 *
 * Who may reset whom follows the same hierarchy as every other write here
 * (assertCanManage): an admin can reset employees but not other admins, and
 * only a super admin can reset an admin. Deliberately unlike forceLogout,
 * which is looser on purpose.
 */
/**
 * POST /api/admin/employees/:employee_id/profile
 *   body: { full_name, designation }
 *
 * Change the name somebody is shown by.
 *
 * Until this existed there was NO WAY to correct a name once an account had
 * been created. Every account made from the panel took its name from the
 * login username, because the create dialog never asked for one — so a real
 * company's employee list read as a column of logins, and a typo in somebody's
 * name was permanent short of editing the database by hand.
 *
 * The name is not decoration. Chat attributes messages by it, reports are read
 * by it, and the audit log records it. A wrong one is wrong in all three.
 *
 * OLD MESSAGES KEEP THE OLD NAME, deliberately. Chat stores sender_name on the
 * message when it is sent, so renaming somebody today does not rewrite what
 * their name was last year. That is what makes the archive an honest record
 * rather than a view of the present.
 */
exports.updateProfile = async (req, res) => {
    const { employee_id } = req.params;
    if (!employee_id) {
        return res.status(400).json({ success: false, message: "employee_id required" });
    }
    if (!(await assertCanManage(req, res, employee_id))) return;

    const raw = req.body || {};
    const hasName = "full_name" in raw;
    const hasRole = "designation" in raw;

    // THE FIELDS ONLY A SUPER ADMIN MAY SET.
    //
    // An ordinary admin manages people day to day; who somebody reports to,
    // which department they belong to, when they joined and whether they are
    // on notice are the company's record of them, not a working detail. The
    // employee cannot touch any of it at all — their own page is read-only
    // here, which is the point of a monitoring product.
    const ORG_FIELDS = ["department", "reporting_manager", "joining_date",
                        "employment_status"];
    const org = ORG_FIELDS.filter((f) => f in raw);
    if (org.length && req.employee?.role !== ROLE_SUPER_ADMIN) {
        return res.status(403).json({
            success: false,
            message: "Only a super admin can change department, manager, "
                   + "joining date or employment status.",
        });
    }

    if (!hasName && !hasRole && !org.length) {
        return res.status(400).json({ success: false, message: "Nothing to change" });
    }

    const STATUSES = ["active", "probation", "notice_period", "resigned", "terminated"];
    if ("employment_status" in raw && raw.employment_status !== null
        && !STATUSES.includes(String(raw.employment_status))) {
        return res.status(400).json({
            success: false,
            message: `employment_status must be one of ${STATUSES.join(", ")}`,
        });
    }
    if ("joining_date" in raw && raw.joining_date
        && !/^\d{4}-\d{2}-\d{2}$/.test(String(raw.joining_date))) {
        return res.status(400).json({
            success: false, message: "joining_date must be YYYY-MM-DD",
        });
    }
    if ("reporting_manager" in raw && raw.reporting_manager
        && String(raw.reporting_manager) === employee_id) {
        return res.status(400).json({
            success: false, message: "Somebody cannot report to themselves",
        });
    }

    const fullName = String(raw.full_name ?? "").trim();
    const designation = String(raw.designation ?? "").trim();

    // The column is VARCHAR(120); a longer value is a database error rather
    // than a message anybody can act on.
    if (fullName.length > 120) {
        return res.status(400).json({
            success: false, message: "Name is too long — 120 characters at most.",
        });
    }
    if (designation.length > 120) {
        return res.status(400).json({
            success: false, message: "Designation is too long — 120 characters at most.",
        });
    }
    // An empty name would put the account back to being shown by its login
    // username, which is the state this endpoint exists to get out of.
    if (hasName && !fullName) {
        return res.status(400).json({
            success: false, message: "A name is required.",
        });
    }

    try {
        const before = await pool.query(
            `SELECT username, full_name FROM employees WHERE employee_id = $1`,
            [employee_id]);
        if (before.rows.length === 0) {
            return res.status(404).json({ success: false, message: "Employee not found" });
        }
        const was = before.rows[0].full_name || before.rows[0].username;

        // COALESCE with a NULL parameter for anything not sent: a field that
        // was not mentioned keeps what it had. Sending it explicitly as null
        // is how it gets cleared, which is why each has its own "was it
        // present" flag rather than relying on the value alone.
        const updated = await pool.query(
            `UPDATE employees
                SET full_name         = COALESCE($2, full_name),
                    designation       = COALESCE($3, designation),
                    department        = CASE WHEN $4::boolean THEN $5 ELSE department END,
                    reporting_manager = CASE WHEN $6::boolean THEN $7 ELSE reporting_manager END,
                    joining_date      = CASE WHEN $8::boolean THEN $9::date ELSE joining_date END,
                    employment_status = CASE WHEN $10::boolean THEN $11 ELSE employment_status END
              WHERE employee_id = $1
              RETURNING employee_id, username, full_name, designation, role,
                        department, reporting_manager, joining_date, employment_status`,
            [employee_id,
             hasName ? fullName : null,
             hasRole ? designation : null,
             "department" in raw, raw.department || null,
             "reporting_manager" in raw, raw.reporting_manager || null,
             "joining_date" in raw, raw.joining_date || null,
             "employment_status" in raw, raw.employment_status || null]
        );

        // On the record. Renaming somebody changes how every report and every
        // conversation reads, so who did it has to be answerable.
        if (hasName && fullName !== was) {
            await pool.query(
                `INSERT INTO activity_logs (employee_id, activity) VALUES ($1, $2)`,
                [employee_id,
                 `NAME CHANGED : "${was}" -> "${fullName}" by ${req.employee?.employee_id || "an admin"}`]
            ).catch(() => {});
        }

        return res.json({ success: true, employee: updated.rows[0] });
    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

exports.resetPassword = async (req, res) => {
    const { employee_id } = req.params;
    const { new_password } = req.body || {};

    if (!employee_id) {
        return res.status(400).json({ success: false, message: "employee_id required" });
    }
    if (!(await assertCanManage(req, res, employee_id))) return;

    try {
        const target = await pool.query(
            `SELECT employee_id, username FROM employees WHERE employee_id = $1`,
            [employee_id]
        );
        if (target.rows.length === 0) {
            return res.status(404).json({ success: false, message: "Employee not found" });
        }
        const employee = target.rows[0];

        const password = new_password || generateTemporaryPassword();
        const problem = validatePassword(password, {
            username:   employee.username,
            employeeId: employee.employee_id,
        });
        if (problem) {
            return res.status(400).json({ success: false, message: problem });
        }

        const bcrypt = require("bcryptjs");
        const hashed = await bcrypt.hash(password, 10);

        await pool.query(
            `UPDATE employees
                SET password = $1, must_change_password = true, password_changed_at = NOW()
              WHERE employee_id = $2`,
            [hashed, employee_id]
        );

        // Sign the account out everywhere. Without this, a session already
        // open on the employee's machine keeps working on the old password,
        // so a reset issued because an account was compromised would not
        // actually end the intruder's access.
        await endSession(pool, employee_id);

        await pool.query(
            `INSERT INTO activity_logs (employee_id, activity) VALUES ($1, $2)`,
            [employee_id, `PASSWORD RESET : by ${req.employee?.employee_id || "an admin"}`]
        ).catch(() => {});

        return res.json({
            success: true,
            message: `Password reset for ${employee_id}. They must change it at next login.`,
            // Shown once in the admin panel and never stored anywhere.
            temporary_password: password,
        });

    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

// ──────────────────────────────────────────────────────────────────────────────
//  Holidays — company-wide non-working dates
//
//  Kept in their own table rather than in employee_configs because a holiday
//  belongs to the calendar, not to a person, and the admin needs to list and
//  remove them one at a time.
//
//  The client never calls these. It receives the dates that matter to it
//  through /config/sync, which sends a window around today rather than the
//  whole table.
// ──────────────────────────────────────────────────────────────────────────────

const ISO_DATE = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;

exports.getHolidays = async (req, res) => {
    try {
        const result = await pool.query(
            `SELECT to_char(holiday_date, 'YYYY-MM-DD') AS holiday_date,
                    name, created_by
               FROM holidays
              ORDER BY holiday_date DESC`
        );
        return res.json({ success: true, holidays: result.rows });
    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

exports.addHoliday = async (req, res) => {
    const { holiday_date, name } = req.body || {};

    // Validated here rather than left to Postgres: an invalid date would
    // otherwise come back as a raw type error that leaks the column type and
    // means nothing to the admin who typed it.
    if (!ISO_DATE.test(String(holiday_date || ""))) {
        return res.status(400).json({
            success: false,
            message: "holiday_date must be a real date in YYYY-MM-DD format",
        });
    }
    const label = String(name || "").trim();
    if (!label) {
        return res.status(400).json({ success: false, message: "A name is required" });
    }
    if (label.length > 120) {
        return res.status(400).json({ success: false, message: "Name must be 120 characters or fewer" });
    }

    try {
        await pool.query(
            `INSERT INTO holidays (holiday_date, name, created_by)
             VALUES ($1, $2, $3)
             ON CONFLICT (holiday_date) DO UPDATE
                 SET name = EXCLUDED.name, created_by = EXCLUDED.created_by`,
            [holiday_date, label, req.employee?.employee_id || null]
        );
        return res.json({ success: true, message: `${holiday_date} saved as ${label}` });
    } catch (err) {
        // A date Postgres rejects despite the regex (30 February) lands here.
        if (err.code === "22008" || err.code === "22007") {
            return res.status(400).json({ success: false, message: "That is not a real date" });
        }
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

exports.deleteHoliday = async (req, res) => {
    const { holiday_date } = req.params;

    if (!ISO_DATE.test(String(holiday_date || ""))) {
        return res.status(400).json({
            success: false,
            message: "holiday_date must be a date in YYYY-MM-DD format",
        });
    }

    try {
        const result = await pool.query(
            `DELETE FROM holidays WHERE holiday_date = $1`, [holiday_date]
        );
        if (result.rowCount === 0) {
            return res.status(404).json({ success: false, message: "No holiday on that date" });
        }
        return res.json({ success: true, message: `${holiday_date} removed` });
    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

// ──────────────────────────────────────────────────────────────────────────────
//  Data retention — company-wide, and the only setting that deletes things
// ──────────────────────────────────────────────────────────────────────────────

// Floors exist because a retention period is a delete instruction. Someone
// typing 1 where they meant 100 would take the audit trail with it, and the
// rows are gone — there is no undo beyond last night's backup.
const RETENTION_LIMITS = {
    log_retention_days:        { min: 7,  max: 3650, label: "Activity logs" },
    screenshot_retention_days: { min: 7,  max: 3650, label: "Screenshots" },
    attendance_retention_days: { min: 90, max: 3650, label: "Attendance" },
    // A higher floor on purpose. This one covers the record of what
    // administrators did, and "who reset that password" is asked months
    // later or not at all — a period short enough to be convenient defeats
    // the point of keeping it.
    audit_log_retention_days:  { min: 180, max: 3650, label: "Admin actions" },
};

exports.getRetention = async (req, res) => {
    try {
        const rows = await pool.query(
            `SELECT key, value, updated_at, updated_by FROM app_settings
              WHERE key = ANY($1)`,
            [Object.keys(RETENTION_LIMITS)]
        );
        const settings = {};
        for (const key of Object.keys(RETENTION_LIMITS)) {
            const row = rows.rows.find((r) => r.key === key);
            settings[key] = row ? Number(row.value) : null;
        }

        // How much the CURRENT settings would remove if the purge ran now.
        // A retention page that does not say what it is about to delete is
        // asking to be set wrong.
        const preview = await pool.query(
            `SELECT
               (SELECT COUNT(*) FROM activity_logs
                 WHERE created_at < NOW() - ($1 || ' days')::interval)  AS logs,
               (SELECT COUNT(*) FROM screenshots
                 WHERE created_at < NOW() - ($2 || ' days')::interval)  AS screenshots,
               (SELECT COUNT(*) FROM attendance
                 WHERE login_time < NOW() - ($3 || ' days')::interval)  AS attendance`,
            [settings.log_retention_days ?? 90,
             settings.screenshot_retention_days ?? 180,
             settings.attendance_retention_days ?? 730]
        );

        return res.json({
            success: true,
            settings,
            limits: RETENTION_LIMITS,
            would_delete: {
                activity_logs: Number(preview.rows[0].logs),
                screenshots:   Number(preview.rows[0].screenshots),
                attendance:    Number(preview.rows[0].attendance),
            },
            updated_at: rows.rows[0]?.updated_at || null,
        });
    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

exports.saveRetention = async (req, res) => {
    const body = req.body || {};

    const updates = [];
    for (const [key, rule] of Object.entries(RETENTION_LIMITS)) {
        if (body[key] === undefined) continue;
        const days = parseInt(body[key], 10);
        if (!Number.isFinite(days) || days < rule.min || days > rule.max) {
            return res.status(400).json({
                success: false,
                message: `${rule.label} must be kept between ${rule.min} and ${rule.max} days`,
            });
        }
        updates.push([key, String(days)]);
    }
    if (updates.length === 0) {
        return res.status(400).json({ success: false, message: "Nothing to save" });
    }

    try {
        for (const [key, value] of updates) {
            await pool.query(
                `INSERT INTO app_settings (key, value, updated_at, updated_by)
                 VALUES ($1, $2, NOW(), $3)
                 ON CONFLICT (key) DO UPDATE
                     SET value = $2, updated_at = NOW(), updated_by = $3`,
                [key, value, req.employee?.employee_id || null]
            );
        }
        await pool.query(
            `INSERT INTO activity_logs (employee_id, activity) VALUES ($1, $2)`,
            [req.employee?.employee_id || null,
             `RETENTION CHANGED : ${updates.map(([k, v]) => `${k}=${v}`).join(", ")}`]
        ).catch(() => {});

        return res.json({ success: true, message: "Retention updated" });
    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

/**
 * Delete specific screenshots.
 *
 * Until now the only way to remove a capture was to delete the whole
 * employee. A screenshot that caught something private — a bank page, a
 * personal message, somebody else's screen — could not be removed at all,
 * which is a poor answer to give the person it belongs to.
 *
 * Deliberately NOT offered for activity logs. An audit trail an admin can
 * edit is not an audit trail; the whole value of it is that it cannot be
 * quietly tidied. Old logs still go on their own through retention.
 *
 * The deletion itself is written to the audit log. Removing evidence is
 * exactly the action that has to leave a trace of who removed it.
 *
 * Role rules follow assertCanManage: an admin can delete an employee's
 * captures but not another admin's, and only a super admin can touch a
 * super admin's.
 */
exports.deleteScreenshots = async (req, res) => {
    const { ids } = req.body || {};

    if (!Array.isArray(ids) || ids.length === 0) {
        return res.status(400).json({ success: false, message: "ids must be a non-empty array" });
    }
    if (ids.length > 500) {
        return res.status(400).json({ success: false, message: "At most 500 at a time" });
    }
    const numeric = ids.map((n) => parseInt(n, 10)).filter(Number.isFinite);
    if (numeric.length !== ids.length) {
        return res.status(400).json({ success: false, message: "ids must all be numbers" });
    }

    try {
        // Read them first: the filenames are needed to remove the files, and
        // the owners are needed to check the caller may touch them.
        const rows = await pool.query(
            `SELECT s.id, s.employee_id, s.file_name, e.role
               FROM screenshots s
               LEFT JOIN employees e ON e.employee_id = s.employee_id
              WHERE s.id = ANY($1)`,
            [numeric]
        );
        if (rows.rows.length === 0) {
            return res.status(404).json({ success: false, message: "No matching screenshots" });
        }

        // Every owner must be one the caller is allowed to manage — a bulk
        // delete must not become a way around a per-employee rule.
        for (const owner of new Set(rows.rows.map((r) => r.employee_id))) {
            const role = rows.rows.find((r) => r.employee_id === owner)?.role;
            const denial = canManage(req.employee, owner, role);
            if (denial) {
                return res.status(403).json({ success: false, message: denial });
            }
        }

        const result = await pool.query(
            `DELETE FROM screenshots WHERE id = ANY($1)`, [numeric]
        );

        // Files after the rows, and never fatal: a file left behind is an
        // orphan the nightly purge sweeps up, whereas failing here would
        // leave the caller thinking nothing was deleted when the rows are
        // already gone.
        let filesRemoved = 0;
        try {
            const fs = require("fs");
            const pathModule = require("path");
            const uploadDir = process.env.UPLOAD_DIR
                ? pathModule.resolve(process.env.UPLOAD_DIR)
                : pathModule.resolve(__dirname, "../uploads/screenshots");
            for (const row of rows.rows) {
                if (!row.file_name) continue;
                const safe = pathModule.basename(String(row.file_name));
                const full = pathModule.resolve(uploadDir, safe);
                if (!full.startsWith(uploadDir + pathModule.sep)) continue;
                try { fs.unlinkSync(full); filesRemoved += 1; } catch (_) {}
            }
        } catch (fileError) {
            console.error("[delete] screenshot file cleanup failed:", fileError.message);
        }

        const owners = [...new Set(rows.rows.map((r) => r.employee_id))].join(", ");
        await pool.query(
            `INSERT INTO activity_logs (employee_id, activity) VALUES ($1, $2)`,
            [req.employee?.employee_id || null,
             `SCREENSHOTS DELETED : ${result.rowCount} capture(s) of ${owners}`]
        ).catch(() => {});

        return res.json({
            success: true,
            deleted: result.rowCount,
            files_removed: filesRemoved,
            message: `${result.rowCount} screenshot(s) deleted`,
        });

    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

/**
 * The next fortnight for one employee: which days they work, which they do
 * not, and why.
 *
 * Weekly off is the only setting on the Configuration page whose effect
 * cannot be seen when you set it. Tick Sunday and everything looks the same
 * until Sunday, and if it silently failed to save there is nothing to
 * notice. That is the whole reason it was reported as "I can't verify it
 * works".
 *
 * This answers it directly: the same rules the client's scheduler applies,
 * run over the coming days, so the setting can be checked the moment it is
 * made.
 */
exports.getUpcomingDays = async (req, res) => {
    const { employee_id } = req.params;
    const days = Math.min(Math.max(parseInt(req.query.days, 10) || 14, 1), 60);

    try {
        const one = employee_id && employee_id !== "global" ? employee_id : null;

        let config = { rows: [] };
        if (one) {
            config = await pool.query(
                `SELECT weekly_offs, shift_start, shift_end
                   FROM employee_configs WHERE employee_id = $1 LIMIT 1`,
                [one]
            );
        }
        if (config.rows.length === 0) {
            config = await pool.query(
                `SELECT weekly_offs, shift_start, shift_end
                   FROM employee_configs WHERE employee_id IS NULL LIMIT 1`
            );
        }
        const weeklyOffs = config.rows[0]?.weekly_offs || "";

        const holidayRows = await pool.query(
            `SELECT to_char(holiday_date, 'YYYY-MM-DD') AS d, name
               FROM holidays
              WHERE holiday_date BETWEEN ${istToday()} AND ${istToday()} + $1::int`,
            [days]
        );
        const holidays = new Map(holidayRows.rows.map((r) => [r.d, r.name]));

        // Start from the IST date, not the server's — the whole system is IST
        // and a UTC-based "today" would shift the list by a day for five and
        // a half hours every night.
        const startRow = await pool.query(`SELECT ${istToday()}::text AS d`);
        let cursor = startRow.rows[0].d;

        const out = [];
        for (let i = 0; i < days; i += 1) {
            const holidayName = holidays.get(cursor);
            const off = isNonWorkingDay(cursor, weeklyOffs, new Set(holidays.keys()));
            out.push({
                date: cursor,
                weekday: new Date(`${cursor}T00:00:00Z`)
                    .toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" }),
                working: !off,
                reason: holidayName ? `Holiday — ${holidayName}`
                      : off ? "Weekly off"
                      : null,
            });
            cursor = shiftIsoDate(cursor, 1);
        }

        return res.json({
            success: true,
            employee_id: one,
            weekly_offs: weeklyOffs,
            shift: config.rows[0]?.shift_start && config.rows[0]?.shift_end
                ? `${String(config.rows[0].shift_start).slice(0, 5)}–${String(config.rows[0].shift_end).slice(0, 5)}`
                : null,
            days: out,
        });
    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

/**
 * Suspend or restore an account.
 *
 * Force logout ends a session; it does not stop the person signing back in a
 * second later. Suspension is the state that persists — through sign-out,
 * restart and token expiry — until an administrator lifts it.
 *
 * Who may do it to whom is assertCanManage, the same rule as every other
 * write here: an admin may suspend employees, a super admin may suspend
 * admins as well, and a super admin cannot be suspended by anyone.
 *
 * Suspending also clears the session. Leaving the token alive would mean a
 * suspended employee keeps working until it expires, which is the exact gap
 * force logout already had.
 */
exports.setSuspended = async (req, res) => {
    const { employee_id } = req.params;
    const suspended = req.body?.suspended === true || req.body?.suspended === "true";

    if (!employee_id) {
        return res.status(400).json({ success: false, message: "employee_id required" });
    }

    // Suspending yourself locks you out of the panel that could undo it. For
    // the last super admin that is unrecoverable without database access.
    if (employee_id === req.employee?.employee_id) {
        return res.status(400).json({
            success: false,
            message: "You cannot suspend your own account.",
        });
    }
    if (!(await assertCanManage(req, res, employee_id))) return;

    try {
        const target = await pool.query(
            `SELECT employee_id, username, role, suspended
               FROM employees WHERE employee_id = $1`,
            [employee_id]
        );
        if (target.rows.length === 0) {
            return res.status(404).json({ success: false, message: "Employee not found" });
        }
        const employee = target.rows[0];

        if (employee.suspended === suspended) {
            return res.json({
                success: true,
                suspended,
                message: `${employee.username} is already ${suspended ? "suspended" : "active"}`,
            });
        }

        await pool.query(
            `UPDATE employees
                SET suspended = $1,
                    suspended_at = CASE WHEN $1 THEN NOW() ELSE NULL END,
                    suspended_by = CASE WHEN $1 THEN $2 ELSE NULL END
              WHERE employee_id = $3`,
            [suspended, req.employee?.employee_id || null, employee_id]
        );

        if (suspended) {
            // End the session now. Without this a suspended employee keeps
            // working until their token expires — up to a day — which is the
            // gap that made force logout insufficient in the first place.
            await endSession(pool, employee_id);
        }

        await pool.query(
            `INSERT INTO activity_logs (employee_id, activity) VALUES ($1, $2)`,
            [employee_id,
             `${suspended ? "ACCOUNT SUSPENDED" : "ACCOUNT UNSUSPENDED"} : by ${req.employee?.employee_id || "an admin"}`]
        ).catch(() => {});

        return res.json({
            success: true,
            suspended,
            message: suspended
                ? `${employee.username} is suspended and has been signed out`
                : `${employee.username} can sign in again`,
        });

    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

exports.getConfig = async (req, res) => {
    const { employee_id } = req.params;
    const isGlobal = employee_id === "global";

    try {
        const result = await pool.query(
            `SELECT * FROM employee_configs
             WHERE ${isGlobal ? "employee_id IS NULL" : "employee_id = $1"}
             ORDER BY updated_at DESC LIMIT 1`,
            isGlobal ? [] : [employee_id]
        );

        const DEFAULT = {
            screenshot_min_minutes:  3,
            screenshot_max_minutes:  10,
            screenshots_per_day:     10,
            upload_interval_minutes: 60,
            idle_threshold_seconds:  60,
            force_logout:            false,
            verbose_logging:         false,
            shift_start:             null,
            shift_end:               null,
            weekly_offs:             "",
            late_grace_minutes:      10,
        };

        // BUG FIX: jis employee ka apna config row nahi hai, uske liye pehle
        // seedha hardcoded DEFAULT return hota tha — GLOBAL config nahi.
        // Matlab admin panel me us employee ke liye 3/10/3/60/60 dikhta tha,
        // jabki asal me wo employee /config/sync se GLOBAL values pe chal
        // raha hota tha. Admin ko galat values dikhtin, aur Save dabate hi
        // wahi galat values us employee pe permanently likh jaatin (global
        // se inherit karna band ho jaata). Ab global row pe fall back karte
        // hain — bilkul waise hi jaise config.controller karta hai.
        let row = result.rows[0];
        // `inherited` = ye values employee ka apna override NAHI hai, global
        // default se aa rahi hain. Response me ye flag pehle nahi tha, is
        // liye admin panel me dono cases bilkul ek jaise dikhte the — admin
        // ko pata hi nahi chalta tha ki wo employee-specific value dekh raha
        // hai ya sabki common value. Ab UI banner me saaf likha jaata hai.
        let inherited = false;
        if (!row && !isGlobal) {
            const globalResult = await pool.query(
                `SELECT * FROM employee_configs WHERE employee_id IS NULL LIMIT 1`
            );
            row = globalResult.rows[0];
            inherited = true;
        }

        const config = { ...DEFAULT, ...(row || {}) };
        // employee_id hamesha wahi rakho jo maanga gaya tha — warna global
        // row se NULL leak ho ke UI confuse ho jaata hai.
        config.employee_id = isGlobal ? null : employee_id;
        config.inherited   = isGlobal ? false : inherited;

        return res.json({ success: true, config });
    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

exports.saveConfig = async (req, res) => {
    let {
        employee_id = null,
        screenshot_min_minutes = 3,
        screenshot_max_minutes = 10,
        screenshots_per_day = 10,
        upload_interval_minutes = 60,
        idle_threshold_seconds = 60,
        force_logout = false,
        verbose_logging = false,
        shift_start = undefined,
        shift_end = undefined,
        weekly_offs = undefined,
        late_grace_minutes = 10,
    } = req.body || {};


    // BUG FIX: getConfig `"global"` ko global-default ke liye sentinel maanta
    // hai (GET /admin/config/global), lekin saveConfig sirf `null` ko global
    // maanta tha. Jo bhi caller `{"employee_id":"global"}` bhejta (deploy.sh
    // yehi bhejta hai) uske liye ek asli employee row ban jaati thi — ek aise
    // employee_id ke naam pe jo employees table me exist hi nahi karta — aur
    // asli global config kabhi update hi nahi hoti thi. Ab dono endpoints ek
    // hi sentinel samajhte hain.
    if (employee_id === "global" || employee_id === "") {
        employee_id = null;
    }

    // ROLE GUARD: baaki endpoints (delete / force-logout / verbose) pe ye
    // check tha lekin saveConfig pe REH GAYA tha — ek admin doosre admin
    // (ya super_admin) ka config badal sakta tha. Global config (employee_id
    // null) sab admins ke liye allowed hai.
    if (employee_id !== null && !(await assertCanManage(req, res, employee_id))) return;

    // Range validation
    const min_ss = parseInt(screenshot_min_minutes);
    const max_ss = parseInt(screenshot_max_minutes);
    const count  = parseInt(screenshots_per_day);
    const upload = parseInt(upload_interval_minutes);
    const idle   = parseInt(idle_threshold_seconds);

    if (isNaN(min_ss) || min_ss < 1 || min_ss > 60)
        return res.status(400).json({ success: false, message: "screenshot_min_minutes must be 1–60" });
    if (isNaN(max_ss) || max_ss < 1 || max_ss > 120)
        return res.status(400).json({ success: false, message: "screenshot_max_minutes must be 1–120" });
    if (min_ss > max_ss)
        return res.status(400).json({ success: false, message: "screenshot_min_minutes must be ≤ screenshot_max_minutes" });
    if (isNaN(count) || count < 1 || count > 20)
        return res.status(400).json({ success: false, message: "screenshots_per_day must be 1–20" });
    if (isNaN(upload) || upload < 1 || upload > 1440)
        return res.status(400).json({ success: false, message: "upload_interval_minutes must be 1–1440" });
    // Idle threshold ab 10–150 sec (admin panel spinbox ke saath match karta
    // hai). Pehle server 3600 tak allow karta tha jabki UI 600 tak — dono
    // out of sync the aur dono hi requirement se zyada the.
    if (isNaN(idle) || idle < 10 || idle > 150)
        return res.status(400).json({ success: false, message: "idle_threshold_seconds must be 10–150" });
    // 0 means "flag any lateness at all"; 120 is as generous as a grace
    // period can be before it stops meaning anything.
    const grace = parseInt(late_grace_minutes);
    if (isNaN(grace) || grace < 0 || grace > 120)
        return res.status(400).json({ success: false, message: "late_grace_minutes must be 0–120" });

    try {
        // BUG FIX: shift_start/shift_end pehle bina kisi validation ke seedha
        // Postgres ko TIME column me chale jaate the. "25:99", "abcde", "9"
        // jaisi value pe Postgres apna RAW error phenk deta tha, jo client
        // tak pahunch jaata:
        //     "invalid input syntax for type time: \"abcde\""
        //     "date/time field value out of range: \"25:99\""
        // Do problem: (1) admin ko samajh na aane wala technical error,
        // (2) internal DB type/schema ka detail leak hota hai.
        // Ab pehle hi HH:MM format check karke saaf 400 dete hain.
        const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
        const normaliseTime = (value, label) => {
            if (value === undefined) return undefined;
            const text = String(value).trim().slice(0, 5);
            if (!TIME_RE.test(text)) {
                const error = new Error(
                    `${label} must be a 24-hour time in HH:MM format (00:00–23:59).`
                );
                error.userFacing = true;
                throw error;
            }
            return text;
        };

        // Weekly offs arrive as ISO weekday numbers (1 = Monday ... 7 =
        // Sunday), either as an array or already comma-joined. Normalised to
        // a sorted, de-duplicated string so the value the client diffs
        // against is stable — '7,1' and '1,7' mean the same thing, and an
        // unstable ordering would make every sync look like a change and
        // rebuild the schedule every five seconds.
        let weeklyOffsStr;
        if (weekly_offs !== undefined) {
            const parts = (Array.isArray(weekly_offs)
                ? weekly_offs
                : String(weekly_offs).split(","))
                .map((d) => parseInt(String(d).trim(), 10))
                .filter((d) => Number.isInteger(d) && d >= 1 && d <= 7);
            const unique = [...new Set(parts)].sort((a, b) => a - b);
            if (unique.length === 7) {
                return res.status(400).json({
                    success: false,
                    message: "Every day cannot be a weekly off — at least one working day is required.",
                });
            }
            weeklyOffsStr = unique.join(",");
        }

        let shiftStartStr, shiftEndStr;
        try {
            shiftStartStr = normaliseTime(shift_start, "Shift start time");
            shiftEndStr   = normaliseTime(shift_end,   "Shift end time");
        } catch (error) {
            if (error.userFacing) {
                return res.status(400).json({ success: false, message: error.message });
            }
            throw error;
        }

        if (employee_id === null) {
            const existing = await pool.query(
                `SELECT id FROM employee_configs WHERE employee_id IS NULL LIMIT 1`
            );

            if (existing.rows.length > 0) {
                await pool.query(
                    `UPDATE employee_configs
                     SET screenshot_min_minutes=$1, screenshot_max_minutes=$2,
                         screenshots_per_day=$3, upload_interval_minutes=$4,
                         idle_threshold_seconds=$5, force_logout=$6,
                         verbose_logging=$7,
                         shift_start = COALESCE($8, shift_start),
                         shift_end   = COALESCE($9, shift_end),
                         weekly_offs = COALESCE($10, weekly_offs),
                         late_grace_minutes = $11,
                         updated_at=NOW()
                     WHERE employee_id IS NULL`,
                    [min_ss, max_ss, count, upload, idle, force_logout, verbose_logging, shiftStartStr, shiftEndStr, weeklyOffsStr, grace]
                );
            } else {
                await pool.query(
                    `INSERT INTO employee_configs
                     (employee_id, screenshot_min_minutes, screenshot_max_minutes,
                      screenshots_per_day, upload_interval_minutes, idle_threshold_seconds,
                      force_logout, verbose_logging, shift_start, shift_end,
                      weekly_offs, late_grace_minutes, updated_at)
                     VALUES (NULL,$1,$2,$3,$4,$5,$6,$7,$8,$9,COALESCE($10,''),$11,NOW())`,
                    [min_ss, max_ss, count, upload, idle, force_logout, verbose_logging, shiftStartStr, shiftEndStr, weeklyOffsStr, grace]
                );
            }
        } else {
            await pool.query(
                `INSERT INTO employee_configs
                 (employee_id, screenshot_min_minutes, screenshot_max_minutes,
                  screenshots_per_day, upload_interval_minutes, idle_threshold_seconds,
                  force_logout, verbose_logging, shift_start, shift_end,
                  weekly_offs, late_grace_minutes, updated_at)
                 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,COALESCE($11,''),$12,NOW())
                 ON CONFLICT (employee_id) DO UPDATE SET
                     screenshot_min_minutes = EXCLUDED.screenshot_min_minutes,
                     screenshot_max_minutes = EXCLUDED.screenshot_max_minutes,
                     screenshots_per_day = EXCLUDED.screenshots_per_day,
                     upload_interval_minutes = EXCLUDED.upload_interval_minutes,
                     idle_threshold_seconds = EXCLUDED.idle_threshold_seconds,
                     force_logout = EXCLUDED.force_logout,
                     verbose_logging = EXCLUDED.verbose_logging,
                     shift_start = COALESCE(EXCLUDED.shift_start, employee_configs.shift_start),
                     shift_end   = COALESCE(EXCLUDED.shift_end, employee_configs.shift_end),
                     weekly_offs = COALESCE($11, employee_configs.weekly_offs),
                     late_grace_minutes = EXCLUDED.late_grace_minutes,
                     updated_at = NOW()`,
                [employee_id, min_ss, max_ss, count, upload, idle, force_logout, verbose_logging, shiftStartStr, shiftEndStr, weeklyOffsStr, grace]
            );
        }


        console.log(`[ADMIN CONFIG SAVED] employee_id=${employee_id ?? "global"}`);
        return res.json({ success: true, message: "Config saved." });

    } catch (err) {
        console.error("SAVE CONFIG ERROR:", err);
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

// FAST TOGGLE (Employees tab ke liye) — ek click se verbose_logging flip
// karne ke liye, bina poora config form khole.
exports.toggleVerboseLogging = async (req, res) => {
    const { employee_id, verbose_logging } = req.body || {};

    if (!employee_id) {
        return res.status(400).json({ success: false, message: "employee_id required" });
    }

    if (!(await assertCanManage(req, res, employee_id))) return;

    try {
        await pool.query(
            `INSERT INTO employee_configs (employee_id, verbose_logging, updated_at)
             VALUES ($1, $2, NOW())
             ON CONFLICT (employee_id)
             DO UPDATE SET verbose_logging = $2, updated_at = NOW()`,
            [employee_id, !!verbose_logging]
        );
        return res.json({
            success: true,
            message: `Verbose logging ${verbose_logging ? "enabled" : "disabled"} for ${employee_id}`
        });
    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

exports.forceLogout = async (req, res) => {
    const { employee_id } = req.body || {};

    if (!employee_id) {
        return res.status(400).json({ success: false, message: "employee_id required" });
    }

    // FORCE LOGOUT ka rule baaki actions se ALAG hai:
    //   - admin KISI KO BHI force logout kar sakta hai (admin ho ya employee)
    //   - sirf super_admin protected hai (use koi nahi nikaal sakta)
    // Ye jaan-boojh kar `assertCanManage()` use nahi karta, kyunki wo admin ko
    // doosre admin pe action lene se rokta hai — jo yahan nahi chahiye.
    try {
        const target = await pool.query(
            `SELECT role FROM employees WHERE employee_id = $1`, [employee_id]
        );
        if (target.rows.length === 0) {
            return res.status(404).json({ success: false, message: "Employee not found" });
        }
        if (target.rows[0].role === ROLE_SUPER_ADMIN
            && req.employee?.role !== ROLE_SUPER_ADMIN) {
            return res.status(403).json({
                success: false,
                message: "The super admin cannot be force logged out."
            });
        }
    } catch (e) {
        return res.status(500).json({ success: false, error: e.message });
    }

    try {
        await pool.query(
            `INSERT INTO employee_configs (employee_id, force_logout, updated_at)
             VALUES ($1, true, NOW())
             ON CONFLICT (employee_id)
             DO UPDATE SET force_logout = true, updated_at = NOW()`,
            [employee_id]
        );

        // Clear the session outright as well as setting the flag.
        //
        // The flag only works if the client is still running to read it. Now
        // that a live session blocks a second login, an employee whose
        // machine died would otherwise be locked out until the two-minute
        // window passed — and if that machine came back, locked out again.
        // Clearing the token frees the account immediately, which is what an
        // admin pressing Force logout actually wants.
        await endSession(pool, employee_id);

        return res.json({ success: true, message: `Force logout set for ${employee_id}` });
    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

exports.getScreenshots = async (req, res) => {
    const { employee_id, date, page = 1 } = req.query;
    const limit  = 20;
    const offset = (page - 1) * limit;

    const conditions = [];
    const values     = [];
    let   idx        = 1;

    if (employee_id) { conditions.push(`employee_id = $${idx++}`); values.push(employee_id); }
    if (date) { conditions.push(`${istDate("created_at")} = $${idx++}`); values.push(date); }

    const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";

    try {
        const result = await pool.query(
            `SELECT id, employee_id, file_name, created_at
             FROM screenshots
             ${where}
             ORDER BY created_at DESC
             LIMIT $${idx} OFFSET $${idx + 1}`,
            [...values, limit, offset]
        );

        const countResult = await pool.query(
            `SELECT COUNT(*) FROM screenshots ${where}`, values
        );

        return res.json({
            success: true,
            data:    result.rows,
            total:   Number(countResult.rows[0].count),
            page:    Number(page),
        });
    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

exports.getLogs = async (req, res) => {
    const { employee_id, date, page = 1, limit: limitParam } = req.query;
    // BUG FIX: pehle limit hardcoded 50 tha, client (LogsWindow admin view)
    // 500 tak request karta tha (poori tarah admin ko saari employees ke
    // logs ek saath dikhane ke liye) lekin server hamesha sirf 50 hi
    // deta tha — client ka limit param completely ignore ho raha tha.
    // Ab client ka diya limit respect hota hai, bas ek sane upper-cap
    // (1000) ke sath taaki koi galti se/jaan-boojh kar bahut bada query
    // na maang le aur DB pe load na daale.
    let limit = parseInt(limitParam, 10);
    if (!Number.isFinite(limit) || limit < 1) limit = 50;
    if (limit > 1000) limit = 1000;
    const offset = (page - 1) * limit;

    const conditions = [];
    const values     = [];
    let   idx        = 1;

    if (employee_id) { conditions.push(`employee_id = $${idx++}`); values.push(employee_id); }
    if (date)        { conditions.push(`${istDate("created_at")} = $${idx++}`); values.push(date); }

    const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";

    try {
        // BUG FIX: pehle yahan broad '%ConfigSyncManager%'/'%SchedulerService%'
        // filter tha — ye Audit Logs (compliance/review ke liye) se
        // meaningful events bhi galti se hide kar deta tha, jaise
        // force_logout actions, shift updates, scheduler start/stop,
        // startup errors — sirf isliye kyunki unka message bhi isi
        // prefix se shuru hota hai jaisa routine verbose noise. Ab sirf
        // specific verbose-only patterns exclude hote hain.
        const noiseWhere = where
            ? `${where}
               AND activity NOT LIKE 'ConfigSyncManager: started%'
               AND activity NOT LIKE 'ConfigSyncManager: stopped%'
               AND activity NOT LIKE 'ConfigSyncManager: backoff%'
               AND activity NOT LIKE 'ConfigSyncManager: sync OK%'
               AND activity NOT LIKE 'ConfigSyncManager: server unreachable%'
               AND activity NOT LIKE 'ConfigSyncManager: request timed out%'
               AND activity NOT LIKE 'ConfigSyncManager: unexpected error%'
               AND activity NOT LIKE 'ConfigSyncManager: HTTP%'
               AND activity NOT LIKE 'SchedulerService: shift times not found%'
               AND activity NOT LIKE 'SchedulerService: shift already ended%'
               AND activity NOT LIKE 'SchedulerService: ConfigSync started%'
               AND activity NOT LIKE 'SchedulerService: screenshot scheduled%'
               AND activity NOT LIKE 'SchedulerService: config updated%'
               AND activity NOT LIKE 'SchedulerService: rescheduled%'
               AND activity NOT LIKE '%SYNC SAVE%'`
            : `WHERE activity NOT LIKE 'ConfigSyncManager: started%'
               AND activity NOT LIKE 'ConfigSyncManager: stopped%'
               AND activity NOT LIKE 'ConfigSyncManager: backoff%'
               AND activity NOT LIKE 'ConfigSyncManager: sync OK%'
               AND activity NOT LIKE 'ConfigSyncManager: server unreachable%'
               AND activity NOT LIKE 'ConfigSyncManager: request timed out%'
               AND activity NOT LIKE 'ConfigSyncManager: unexpected error%'
               AND activity NOT LIKE 'ConfigSyncManager: HTTP%'
               AND activity NOT LIKE 'SchedulerService: shift times not found%'
               AND activity NOT LIKE 'SchedulerService: shift already ended%'
               AND activity NOT LIKE 'SchedulerService: ConfigSync started%'
               AND activity NOT LIKE 'SchedulerService: screenshot scheduled%'
               AND activity NOT LIKE 'SchedulerService: config updated%'
               AND activity NOT LIKE 'SchedulerService: rescheduled%'
               AND activity NOT LIKE '%SYNC SAVE%'`;
        const result = await pool.query(
            `SELECT id, employee_id, activity, created_at
             FROM activity_logs
             ${noiseWhere}
             ORDER BY created_at DESC
             LIMIT $${idx} OFFSET $${idx + 1}`,
            [...values, limit, offset]
        );
        const countResult = await pool.query(
            `SELECT COUNT(*) FROM activity_logs ${noiseWhere}`, values
        );

        return res.json({
            success: true,
            data:    result.rows,
            total:   Number(countResult.rows[0].count),
            page:    Number(page),
        });
    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

exports.getEmployeeDetails = async (req, res) => {
    const { employee_id } = req.params;

    try {
        // "Latest 10" ko sirf TRUE diagnostic/heartbeat noise se saaf karo —
        // pehle ek broad '%ConfigSyncManager%'/'%SchedulerService%' filter
        // tha jo galti se meaningful events (force_logout, shift updated,
        // scheduler started/stopped, startup errors) bhi hide kar deta,
        // kyunki wo bhi isi prefix se shuru hote hain. Ab sirf specific
        // verbose-only message patterns exclude ho rahe hain — jo hamesha
        // (verbose_logging OFF hone par bhi) generate hone wale meaningful
        // events hain, wo dikhte rahenge.
        const recent = await pool.query(
            `SELECT created_at, activity
             FROM activity_logs
             WHERE employee_id = $1
               AND activity NOT LIKE 'ConfigSyncManager: started%'
               AND activity NOT LIKE 'ConfigSyncManager: stopped%'
               AND activity NOT LIKE 'ConfigSyncManager: backoff%'
               AND activity NOT LIKE 'ConfigSyncManager: sync OK%'
               AND activity NOT LIKE 'ConfigSyncManager: server unreachable%'
               AND activity NOT LIKE 'ConfigSyncManager: request timed out%'
               AND activity NOT LIKE 'ConfigSyncManager: unexpected error%'
               AND activity NOT LIKE 'ConfigSyncManager: HTTP%'
               AND activity NOT LIKE 'SchedulerService: shift times not found%'
               AND activity NOT LIKE 'SchedulerService: shift already ended%'
               AND activity NOT LIKE 'SchedulerService: ConfigSync started%'
               AND activity NOT LIKE 'SchedulerService: screenshot scheduled%'
               AND activity NOT LIKE 'SchedulerService: config updated%'
               AND activity NOT LIKE 'SchedulerService: rescheduled%'
             ORDER BY id DESC
             LIMIT 10`,
            [employee_id]
        );

        const screenshots = await pool.query(
            `SELECT COUNT(*) AS count FROM screenshots WHERE employee_id = $1`,
            [employee_id]
        );

        const attendance = await pool.query(
            `SELECT 1 FROM attendance
             WHERE employee_id = $1 AND logout_time IS NULL
               AND login_time > (NOW() AT TIME ZONE 'UTC') - INTERVAL '16 hours' LIMIT 1`,
            [employee_id]
        );

        const isOnline = attendance.rows.length > 0;

        const logsCount = await pool.query(
            `SELECT COUNT(*) AS count FROM activity_logs WHERE employee_id = $1`,
            [employee_id]
        );

        // ─────────────────────────────────────────────────────────────────
        //  ACTIVE / IDLE TIME — session-bounded
        //
        //  BUG: pehle ye SAARE USER ACTIVE/IDLE events le kar consecutive
        //  events ka gap jod deta tha — bina ye dekhe ki beech me app band
        //  thi ya nahi. Matlab agar employee ne shukravar 6 baje app band
        //  ki aur somvar 10 baje kholi, to beech ke 64 GHANTE bhi "active
        //  time" me jud jaate the.
        //
        //  Production data pe iska asar: EMP001 ka Active Time 801:14:31
        //  (33 din) dikh raha tha — jo asal me uske pehle log se ab tak ka
        //  poora wall-clock time tha, kaam ka time nahi.
        //
        //  Ab time sirf ASLI ATTENDANCE SESSIONS ke andar hi count hota hai
        //  (login se logout tak). Session ke bahar ka koi bhi gap ignore.
        //  Har employee ka apna data, apni sessions — kisi ke saath
        //  naainsafi nahi.
        // ─────────────────────────────────────────────────────────────────
        const WINDOW_DAYS = 90;

        const sessions = await pool.query(
            `SELECT login_time,
                    COALESCE(logout_time, (NOW() AT TIME ZONE 'UTC')) AS end_time
             FROM attendance
             WHERE employee_id = $1
               AND login_time > (NOW() AT TIME ZONE 'UTC') - INTERVAL '${WINDOW_DAYS} days'
             ORDER BY login_time ASC`,
            [employee_id]
        );

        // SCALE FIX: pehle is query pe koi LIMIT nahi thi. Ek bhaari user ke
        // laakhon events Node ki memory me aa jaate — aur ye dialog har 10
        // second refresh hota hai.
        const events = await pool.query(
            `SELECT created_at, activity
             FROM activity_logs
             WHERE employee_id = $1
               AND (UPPER(activity) LIKE '%USER ACTIVE%' OR UPPER(activity) LIKE '%USER IDLE%')
               AND created_at > (NOW() AT TIME ZONE 'UTC') - INTERVAL '${WINDOW_DAYS} days'
             ORDER BY created_at ASC
             LIMIT 50000`,
            [employee_id]
        );

        let activeMs = 0;
        let idleMs   = 0;

        const normalizeState = (activity) => {
            const a = (activity || "").toUpperCase();
            if (a.includes("USER IDLE"))   return "IDLE";
            if (a.includes("USER ACTIVE")) return "ACTIVE";
            return null;
        };

        // created_at raw string aati hai (db.js identity type-parser).
        // `new Date(str)` process ki ambient TZ use karta — explicitly UTC.
        const parseUtc = (s) => new Date(String(s).replace(" ", "T") + "Z").getTime();

        const evts = events.rows
            .map(r => ({ t: parseUtc(r.created_at), s: normalizeState(r.activity) }))
            .filter(e => e.s && Number.isFinite(e.t));

        for (const row of sessions.rows) {
            const sStart = parseUtc(row.login_time);
            const sEnd   = parseUtc(row.end_time);
            if (!Number.isFinite(sStart) || !Number.isFinite(sEnd) || sEnd <= sStart) continue;

            // Is session ke andar ke events
            const inSession = evts.filter(e => e.t >= sStart && e.t <= sEnd);

            // Session start se pehle event tak — employee abhi abhi login
            // hua hai, use ACTIVE maano.
            let cursor = sStart;
            let state  = "ACTIVE";

            for (const e of inSession) {
                const dt = e.t - cursor;
                if (dt > 0) {
                    if (state === "ACTIVE") activeMs += dt;
                    else                    idleMs   += dt;
                }
                state  = e.s;
                cursor = e.t;
            }

            // Aakhri event se session end tak
            const tail = sEnd - cursor;
            if (tail > 0) {
                if (state === "ACTIVE") activeMs += tail;
                else                    idleMs   += tail;
            }
        }

        const formatDur = (ms) => {
            const totalSec = Math.floor(ms / 1000);
            const h = String(Math.floor(totalSec / 3600)).padStart(2, "0");
            const m = String(Math.floor((totalSec % 3600) / 60)).padStart(2, "0");
            const s = String(totalSec % 60).padStart(2, "0");
            return `${h}:${m}:${s}`;
        };

        return res.json({
            success: true,
            data: {
                employee_id,
                status:             isOnline ? "online" : "offline",
                active_time:        formatDur(activeMs),
                idle_time:          formatDur(idleMs),
                screenshot_count:   Number(screenshots.rows[0].count || 0),
                activity_log_count: Number(logsCount.rows[0].count || 0),
                recent_activity:    recent.rows.map(r => ({
                    created_at: r.created_at,
                    activity:   r.activity,
                })),
            }
        });

    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

exports.deleteEmployee = async (req, res) => {
    const { employee_id } = req.params;

    // super_admin ko koi delete nahi kar sakta; admin doosre admin ko nahi.
    if (!(await assertCanManage(req, res, employee_id))) return;

    if (req.employee?.employee_id === employee_id) {
        return res.status(400).json({
            success: false,
            message: req.employee?.role === ROLE_SUPER_ADMIN
                ? "You cannot delete your own super admin account. Promote another "
                  + "admin to super admin first, then they can remove you."
                : "You cannot delete your own account."
        });
    }

    // Aakhri super admin kabhi delete na ho — warna koi bhi role manage
    // karne wala nahi bachega aur system permanently lock ho jayega.
    try {
        const target = await pool.query(
            `SELECT role FROM employees WHERE employee_id = $1`, [employee_id]
        );
        if (target.rows[0]?.role === ROLE_SUPER_ADMIN) {
            const supers = await pool.query(
                `SELECT COUNT(*) FROM employees WHERE role = $1`, [ROLE_SUPER_ADMIN]
            );
            if (Number(supers.rows[0].count) <= 1) {
                return res.status(403).json({
                    success: false,
                    message: "This is the only super admin. Promote another admin to "
                           + "super admin before deleting this account."
                });
            }
        }
    } catch (e) {
        return res.status(500).json({ success: false, error: e.message });
    }

    const client = await pool.connect();

    try {
        await client.query("BEGIN");

        const employee = await client.query(
            `SELECT employee_id
             FROM employees
             WHERE employee_id = $1`,
            [employee_id]
        );

        if (employee.rows.length === 0) {
            await client.query("ROLLBACK");
            return res.status(404).json({
                success: false,
                message: "Employee not found"
            });
        }

        await client.query(
            `DELETE FROM employee_configs
             WHERE employee_id = $1`,
            [employee_id]
        );

        // Deleted employee ka session turant revoke karo. token = NULL karo
        // (row DELETE nahi) — verifyToken middleware sirf tab purana token
        // reject karta hai jab active_sessions row EXIST kare aur token
        // mismatch ho; row hi na ho to check skip ho jata hai aur purana
        // (abhi bhi valid) JWT deleted employee ke liye kaam karta rehta
        // — apni natural 24h expiry tak. token=NULL karne se agli hi
        // request pe turant 401 milega.
        await endSession(client, employee_id);

        await client.query(
            `DELETE FROM attendance
             WHERE employee_id = $1`,
            [employee_id]
        );

        // Collect the filenames BEFORE the rows go, then delete the files
        // after the transaction commits.
        //
        // BUG this fixes: deleting an employee removed every trace of them
        // from the database and left their encrypted screenshots sitting on
        // disk forever. Nothing referenced them any more, so they could
        // never be viewed, purged or even found — just an ever-growing
        // folder. At a thousand employees that is tens of gigabytes of files
        // belonging to people who are no longer in the system, which is a
        // data-retention problem as much as a disk one.
        const doomedFiles = await client.query(
            `SELECT file_name FROM screenshots WHERE employee_id = $1`,
            [employee_id]
        );

        await client.query(
            `DELETE FROM screenshots
             WHERE employee_id = $1`,
            [employee_id]
        );

        // Idle totals and the session row, which nothing else clears.
        //
        // Neither table has a foreign key to employees, so nothing sweeps
        // them up on its own: they simply stayed, one row per person per day,
        // for accounts that no longer exist. Not fatal, but it is tracking
        // data about a former employee kept past the point of any use — the
        // same retention problem the screenshot files above were fixed for.
        //
        // The session row is deleted outright rather than blanked. Blanking
        // is right for a logout, where the account remains and the row still
        // means something. Here there is no account left for it to belong to.
        await client.query(
            `DELETE FROM idle_daily WHERE employee_id = $1`,
            [employee_id]
        );

        await client.query(
            `DELETE FROM active_sessions WHERE employee_id = $1`,
            [employee_id]
        );

        await client.query(
            `DELETE FROM activity_logs
             WHERE employee_id = $1`,
            [employee_id]
        );

        await client.query(
            `DELETE FROM employees
             WHERE employee_id = $1`,
            [employee_id]
        );

        await client.query("COMMIT");

        // After the commit, never before: a file removed inside the
        // transaction would be gone even if the transaction rolled back.
        // The rows are already deleted, so a file left behind here is
        // orphaned rather than lost — the safe direction to fail.
        let filesRemoved = 0;
        try {
            const fs = require("fs");
            const path = require("path");
            const uploadDir = process.env.UPLOAD_DIR
                ? path.resolve(process.env.UPLOAD_DIR)
                : path.resolve(__dirname, "../uploads/screenshots");

            for (const row of doomedFiles.rows) {
                if (!row.file_name) continue;
                // Same guard the download path uses: never trust a stored
                // value for filesystem access. basename strips directories,
                // and the resolved path must still sit inside uploadDir.
                const safe = path.basename(String(row.file_name));
                const full = path.resolve(uploadDir, safe);
                if (!full.startsWith(uploadDir + path.sep)) continue;
                try {
                    fs.unlinkSync(full);
                    filesRemoved += 1;
                } catch (_) {
                    // Already gone, or never written. Not worth failing a
                    // delete that has already succeeded in the database.
                }
            }
        } catch (fileError) {
            console.error("[delete] screenshot cleanup failed:", fileError.message);
        }

        return res.json({
            success: true,
            message: `Employee ${employee_id} deleted`
                   + (filesRemoved ? ` (${filesRemoved} screenshot file(s) removed)` : "")
        });

    } catch (err) {
        await client.query("ROLLBACK");
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    } finally {
        client.release();
    }
};

exports.saveShift = async (req, res) => {
    const { employee_id, shift_start, shift_end } = req.body || {};
    if (!employee_id || !shift_start || !shift_end) {
        return res.status(400).json({ success: false, message: "employee_id, shift_start, shift_end required" });
    }

    // HH:MM (24h) format validation — same shape used everywhere else
    // (employee_configs.shift_start/shift_end are TIME columns).
    const timeRe = /^([01]\d|2[0-3]):([0-5]\d)$/;
    const startStr = String(shift_start).trim().slice(0, 5);
    const endStr   = String(shift_end).trim().slice(0, 5);

    if (!timeRe.test(startStr))
        return res.status(400).json({ success: false, message: "shift_start must be HH:MM (24h)" });
    if (!timeRe.test(endStr))
        return res.status(400).json({ success: false, message: "shift_end must be HH:MM (24h)" });
    if (startStr === endStr)
        return res.status(400).json({ success: false, message: "shift_start and shift_end cannot be the same" });

    try {
        await pool.query(
            `INSERT INTO employee_configs (employee_id, shift_start, shift_end, updated_at)
             VALUES ($1, $2, $3, NOW())
             ON CONFLICT (employee_id) DO UPDATE SET
                 shift_start = EXCLUDED.shift_start,
                 shift_end   = EXCLUDED.shift_end,
                 updated_at  = NOW()`,
            [employee_id, startStr, endStr]
        );
        return res.json({ success: true, message: "Shift saved" });
    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

// ──────────────────────────────────────────────────────────────────────────────
//  Role management — SIRF super_admin
//
//  super_admin ("god" role) company ka owner/manager hai:
//    - kisi ka bhi role badal sakta hai (employee <-> admin)
//    - uske upar koi rule nahi lagta
//    - use koi doosra demote/delete/modify NAHI kar sakta — is liye kisi bhi
//      super_admin ka role badalna yahan poori tarah blocked hai (chahe
//      request khud super_admin hi kyun na bheje). Super admin ko badalna ho
//      to wo DB pe deliberate action hona chahiye, ek API call se nahi.
// ──────────────────────────────────────────────────────────────────────────────
exports.changeRole = async (req, res) => {
    const { employee_id } = req.params;
    const { role } = req.body || {};

    if (!employee_id || !role) {
        return res.status(400).json({
            success: false,
            message: "employee_id and role are required"
        });
    }

    if (!VALID_ROLES.includes(role)) {
        return res.status(400).json({
            success: false,
            message: `role must be one of: ${VALID_ROLES.join(", ")}`
        });
    }

    try {
        const target = await pool.query(
            `SELECT role FROM employees WHERE employee_id = $1`, [employee_id]
        );
        if (target.rows.length === 0) {
            return res.status(404).json({ success: false, message: "Employee not found" });
        }

        // ── Super admin ko demote karne ke rules ──
        //
        //  1. Koi bhi super admin KHUD ko demote nahi kar sakta. Pehle kisi
        //     doosre admin ko super admin banao (power transfer), phir wo
        //     aapko hata sakta hai.
        //  2. AAKHRI super admin ko kabhi demote nahi kiya ja sakta — warna
        //     company ke paas koi super admin bachega hi nahi aur role
        //     management hamesha ke liye lock ho jayega (koi promote karne
        //     wala hi nahi bachega).
        if (target.rows[0].role === ROLE_SUPER_ADMIN) {
            if (employee_id === req.employee?.employee_id) {
                return res.status(403).json({
                    success: false,
                    message: "You cannot remove your own super admin role. "
                           + "Promote another admin to super admin first, then they can do it."
                });
            }

            const supers = await pool.query(
                `SELECT COUNT(*) FROM employees WHERE role = $1`, [ROLE_SUPER_ADMIN]
            );
            if (Number(supers.rows[0].count) <= 1) {
                return res.status(403).json({
                    success: false,
                    message: "This is the only super admin. Promote another admin to "
                           + "super admin before removing this one."
                });
            }
        }

        // Sirf super_admin kisi ko promote kar sakta hai (route pehle se
        // superAdminOnly hai — ye defence-in-depth).
        if (role !== "employee" && req.employee?.role !== ROLE_SUPER_ADMIN) {
            return res.status(403).json({
                success: false,
                message: "Only a super admin can promote someone to admin or super admin."
            });
        }

        // Promotion pe bhi wahi caps lagte hain jo creation pe. Target ko
        // count se chhod dete hain — warna same role pe "promote" karna bhi
        // limit hit kar deta.
        if (ROLE_LIMITS[role]) {
            const current = await countRole(role, employee_id);
            if (current >= ROLE_LIMITS[role]) {
                return res.status(409).json({
                    success: false,
                    message: `Limit reached — a maximum of ${ROLE_LIMITS[role]} `
                           + `${role === ROLE_SUPER_ADMIN ? "super admins" : "admins"} `
                           + `are allowed (currently ${current}).`,
                });
            }
        }

        await pool.query(
            `UPDATE employees SET role = $1 WHERE employee_id = $2`,
            [role, employee_id]
        );

        // Role badalne par uska current session turant revoke karo — warna
        // purana JWT (jisme purana role embedded hai) apni 24h expiry tak
        // purane privileges ke saath chalta rehta.
        await endSession(pool, employee_id);

        return res.json({
            success: true,
            message: `${employee_id} is now ${role}. They must sign in again.`
        });

    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};
