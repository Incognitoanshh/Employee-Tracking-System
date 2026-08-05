const pool = require("../config/db");
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

        const searchWhere = search
            ? `WHERE (employee_id ILIKE $1 OR username ILIKE $1 OR role ILIKE $1)`
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
                COALESCE(ec.verbose_logging, false) AS verbose_logging
            FROM (
                SELECT employee_id, username, role, full_name, designation
                FROM employees
                ${searchWhere}
                ORDER BY employee_id ASC
                LIMIT $${p + 1} OFFSET $${p + 2}
            ) e
            LEFT JOIN LATERAL (
                SELECT 1 AS hit
                FROM attendance a
                WHERE a.employee_id = e.employee_id
                  AND a.logout_time IS NULL
                  AND a.login_time > (NOW() AT TIME ZONE 'UTC') - INTERVAL '16 hours'
                LIMIT 1
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
                message: "An employee with this employee_id or username already exists"
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
        await pool.query(
            `UPDATE active_sessions SET token = NULL WHERE employee_id = $1`,
            [employee_id]
        );

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
                         updated_at=NOW()
                     WHERE employee_id IS NULL`,
                    [min_ss, max_ss, count, upload, idle, force_logout, verbose_logging, shiftStartStr, shiftEndStr]
                );
            } else {
                await pool.query(
                    `INSERT INTO employee_configs
                     (employee_id, screenshot_min_minutes, screenshot_max_minutes,
                      screenshots_per_day, upload_interval_minutes, idle_threshold_seconds,
                      force_logout, verbose_logging, shift_start, shift_end, updated_at)
                     VALUES (NULL,$1,$2,$3,$4,$5,$6,$7,$8,$9,NOW())`,
                    [min_ss, max_ss, count, upload, idle, force_logout, verbose_logging, shiftStartStr, shiftEndStr]
                );
            }
        } else {
            await pool.query(
                `INSERT INTO employee_configs
                 (employee_id, screenshot_min_minutes, screenshot_max_minutes,
                  screenshots_per_day, upload_interval_minutes, idle_threshold_seconds,
                  force_logout, verbose_logging, shift_start, shift_end, updated_at)
                 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NOW())
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
                     updated_at = NOW()`,
                [employee_id, min_ss, max_ss, count, upload, idle, force_logout, verbose_logging, shiftStartStr, shiftEndStr]
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
        await client.query(
            `UPDATE active_sessions SET token = NULL WHERE employee_id = $1`,
            [employee_id]
        );

        await client.query(
            `DELETE FROM attendance
             WHERE employee_id = $1`,
            [employee_id]
        );

        await client.query(
            `DELETE FROM screenshots
             WHERE employee_id = $1`,
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

        return res.json({
            success: true,
            message: `Employee ${employee_id} deleted`
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
        await pool.query(
            `UPDATE active_sessions SET token = NULL WHERE employee_id = $1`,
            [employee_id]
        );

        return res.json({
            success: true,
            message: `${employee_id} is now ${role}. They must sign in again.`
        });

    } catch (err) {
        console.error("[500]", req.method, req.originalUrl, err.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};
