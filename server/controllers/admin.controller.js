const pool = require("../config/db");

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
exports.getEmployees = async (req, res) => {
    try {
        const result = await pool.query(`
            SELECT
                e.employee_id,
                e.username,
                e.role,

                -- Status: attendance open session = online (agar recent hai)
                CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM attendance a
                        WHERE a.employee_id = e.employee_id
                          AND a.logout_time IS NULL
                          AND a.login_time > NOW()::timestamp - INTERVAL '16 hours'
                    )
                    THEN 'online'
                    ELSE 'offline'
                END AS status,

                -- FIX #4: last_seen
                --   Online users: NULL (frontend will show "Active now" based on status)
                --   Offline users: max(last logout, last activity log)
                CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM attendance a
                        WHERE a.employee_id = e.employee_id
                          AND a.logout_time IS NULL
                          AND a.login_time > NOW()::timestamp - INTERVAL '16 hours'
                    )
                    THEN NULL
                    ELSE (
                        SELECT GREATEST(
                            COALESCE(
                                (SELECT MAX(a2.logout_time)
                                 FROM attendance a2
                                 WHERE a2.employee_id = e.employee_id
                                   AND a2.logout_time IS NOT NULL),
                                '1970-01-01'::timestamptz
                            ),
                            COALESCE(
                                (SELECT MAX(al.created_at)
                                 FROM activity_logs al
                                 WHERE al.employee_id = e.employee_id),
                                '1970-01-01'::timestamptz
                            )
                        )
                    )
                END AS last_seen,

                -- Verbose logging flag (employee-specific config se)
                COALESCE(
                    (SELECT ec.verbose_logging
                     FROM employee_configs ec
                     WHERE ec.employee_id = e.employee_id
                     ORDER BY ec.updated_at DESC LIMIT 1),
                    false
                ) AS verbose_logging

            FROM employees e
            ORDER BY e.employee_id ASC
        `);

        return res.json({
            success: true,
            data: result.rows.map(row => ({
                ...row,
                // If last_seen is epoch (no real activity), return null — UI shows "Never"
                last_seen: row.last_seen && new Date(row.last_seen).getFullYear() > 1970
                    ? row.last_seen
                    : (row.status === "online" ? null : null)
            }))
        });

    } catch (err) {
        return res.status(500).json({
            success: false,
            error: err.message
        });
    }
};

exports.createEmployee = async (req, res) => {
    const { employee_id, username, password, role = "employee" } = req.body;

    // BUG FIX: pehle empty/missing fields directly DB tak pahunch jaate the.
    if (!employee_id || !username || !password) {
        return res.status(400).json({
            success: false,
            message: "employee_id, username and password are required"
        });
    }

    try {
        const bcrypt = require("bcryptjs");
        const hashedPassword = await bcrypt.hash(password, 10);

        await pool.query(
            `INSERT INTO employees (employee_id, username, password, role)
             VALUES ($1, $2, $3, $4)`,
            [employee_id, username, hashedPassword, role]
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
        return res.status(500).json({ success: false, error: err.message });
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
            screenshot_count:        3,
            upload_interval_minutes: 60,
            idle_threshold_seconds:  60,
            force_logout:            false,
            verbose_logging:         false,
        };

        const row    = result.rows[0] || {};
        const config = { ...DEFAULT, ...row };

        return res.json({ success: true, config });
    } catch (err) {
        return res.status(500).json({ success: false, error: err.message });
    }
};

exports.saveConfig = async (req, res) => {
    const {
        employee_id = null,
        screenshot_min_minutes = 3,
        screenshot_max_minutes = 10,
        screenshot_count = 3,
        upload_interval_minutes = 60,
        idle_threshold_seconds = 60,
        force_logout = false,
        verbose_logging = false,
        shift_start = undefined,
        shift_end = undefined,
    } = req.body;


    // Range validation
    const min_ss = parseInt(screenshot_min_minutes);
    const max_ss = parseInt(screenshot_max_minutes);
    const count  = parseInt(screenshot_count);
    const upload = parseInt(upload_interval_minutes);
    const idle   = parseInt(idle_threshold_seconds);

    if (isNaN(min_ss) || min_ss < 1 || min_ss > 60)
        return res.status(400).json({ success: false, message: "screenshot_min_minutes must be 1–60" });
    if (isNaN(max_ss) || max_ss < 1 || max_ss > 120)
        return res.status(400).json({ success: false, message: "screenshot_max_minutes must be 1–120" });
    if (min_ss > max_ss)
        return res.status(400).json({ success: false, message: "screenshot_min_minutes must be ≤ screenshot_max_minutes" });
    if (isNaN(count) || count < 1 || count > 20)
        return res.status(400).json({ success: false, message: "screenshot_count must be 1–20" });
    if (isNaN(upload) || upload < 1 || upload > 1440)
        return res.status(400).json({ success: false, message: "upload_interval_minutes must be 1–1440" });
    if (isNaN(idle) || idle < 10 || idle > 3600)
        return res.status(400).json({ success: false, message: "idle_threshold_seconds must be 10–3600" });

    try {
        const shiftStartStr = shift_start !== undefined ? String(shift_start).trim().slice(0, 5) : undefined;
        const shiftEndStr   = shift_end   !== undefined ? String(shift_end).trim().slice(0, 5) : undefined;

        if (employee_id === null) {
            const existing = await pool.query(
                `SELECT id FROM employee_configs WHERE employee_id IS NULL LIMIT 1`
            );

            if (existing.rows.length > 0) {
                await pool.query(
                    `UPDATE employee_configs
                     SET screenshot_min_minutes=$1, screenshot_max_minutes=$2,
                         screenshot_count=$3, upload_interval_minutes=$4,
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
                      screenshot_count, upload_interval_minutes, idle_threshold_seconds,
                      force_logout, verbose_logging, shift_start, shift_end, updated_at)
                     VALUES (NULL,$1,$2,$3,$4,$5,$6,$7,$8,$9,NOW())`,
                    [min_ss, max_ss, count, upload, idle, force_logout, verbose_logging, shiftStartStr, shiftEndStr]
                );
            }
        } else {
            await pool.query(
                `INSERT INTO employee_configs
                 (employee_id, screenshot_min_minutes, screenshot_max_minutes,
                  screenshot_count, upload_interval_minutes, idle_threshold_seconds,
                  force_logout, verbose_logging, shift_start, shift_end, updated_at)
                 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NOW())
                 ON CONFLICT (employee_id) DO UPDATE SET
                     screenshot_min_minutes = EXCLUDED.screenshot_min_minutes,
                     screenshot_max_minutes = EXCLUDED.screenshot_max_minutes,
                     screenshot_count = EXCLUDED.screenshot_count,
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
        return res.status(500).json({ success: false, error: err.message });
    }
};

// FAST TOGGLE (Employees tab ke liye) — ek click se verbose_logging flip
// karne ke liye, bina poora config form khole.
exports.toggleVerboseLogging = async (req, res) => {
    const { employee_id, verbose_logging } = req.body;

    if (!employee_id) {
        return res.status(400).json({ success: false, message: "employee_id required" });
    }

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
        return res.status(500).json({ success: false, error: err.message });
    }
};

exports.forceLogout = async (req, res) => {
    const { employee_id } = req.body;

    if (!employee_id) {
        return res.status(400).json({ success: false, message: "employee_id required" });
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
        return res.status(500).json({ success: false, error: err.message });
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
    if (date) { conditions.push(`DATE((created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata') = $${idx++}`); values.push(date); }

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
        return res.status(500).json({ success: false, error: err.message });
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
    if (date)        { conditions.push(`DATE((created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata') = $${idx++}`); values.push(date); }

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
        return res.status(500).json({ success: false, error: err.message });
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
               AND login_time > NOW()::timestamp - INTERVAL '16 hours' LIMIT 1`,
            [employee_id]
        );

        const isOnline = attendance.rows.length > 0;

        const logsCount = await pool.query(
            `SELECT COUNT(*) AS count FROM activity_logs WHERE employee_id = $1`,
            [employee_id]
        );

        const events = await pool.query(
            `SELECT created_at, activity
             FROM activity_logs
             WHERE employee_id = $1
               AND (UPPER(activity) LIKE '%USER ACTIVE%' OR UPPER(activity) LIKE '%USER IDLE%')
             ORDER BY id ASC`,
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

        // BUG FIX: created_at DB se raw string (jaise "2026-07-03 11:33:27")
        // aati hai (db.js mein timestamp type-parser identity rakha gaya
        // hai). `new Date(str)` is string ko Node process ki AMBIENT
        // timezone ke hisaab se parse karta hai — agar kabhi PM2/server
        // ki TZ UTC se badal jaye (abhi UTC hai isliye chal raha hai),
        // to active/idle time silently GALAT ho jayenge (timezone-shifted).
        // parseUtc() se hum string ko explicitly UTC maan ke parse karte
        // hain, chahe process ki TZ kuch bhi ho.
        const parseUtc = (s) => new Date(String(s).replace(" ", "T") + "Z");

        for (let i = 0; i < events.rows.length - 1; i++) {
            const cur  = events.rows[i];
            const next = events.rows[i + 1];
            const state = normalizeState(cur.activity);
            if (!state) continue;

            const dt = parseUtc(next.created_at).getTime() - parseUtc(cur.created_at).getTime();
            if (!Number.isFinite(dt) || dt < 0) continue;

            if (state === "ACTIVE") activeMs += dt;
            if (state === "IDLE")   idleMs   += dt;
        }

        // Add currently running session tail
        if (isOnline && events.rows.length > 0) {
            let latestState     = null;
            let latestEventTime = null;

            for (let i = events.rows.length - 1; i >= 0; i--) {
                const st = normalizeState(events.rows[i].activity);
                if (!st) continue;
                latestState     = st;
                latestEventTime = parseUtc(events.rows[i].created_at).getTime();
                break;
            }

            if (latestState && Number.isFinite(latestEventTime)) {
                const extraMs = Date.now() - latestEventTime;
                if (Number.isFinite(extraMs) && extraMs > 0) {
                    if (latestState === "ACTIVE") activeMs += extraMs;
                    if (latestState === "IDLE")   idleMs   += extraMs;
                }
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
        return res.status(500).json({ success: false, error: err.message });
    }
};

exports.deleteEmployee = async (req, res) => {
    const { employee_id } = req.params;

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
        return res.status(500).json({
            success: false,
            error: err.message
        });
    } finally {
        client.release();
    }
};

exports.saveShift = async (req, res) => {
    const { employee_id, shift_start, shift_end } = req.body;
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
        return res.status(500).json({ success: false, error: err.message });
    }
};
