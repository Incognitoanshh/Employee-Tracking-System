const pool = require("../config/db");

// ──────────────────────────────────────────────────────────────────────────────
//  FIX #3/#4: getEmployees
//  - Status: attendance-based (open session = online, else offline). CORRECT.
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

                -- Status: attendance open session = online
                CASE
                    WHEN EXISTS (
                        SELECT 1
                        FROM attendance a
                        WHERE a.employee_id = e.employee_id
                          AND a.logout_time IS NULL
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
            data: result.rows
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
    } = req.body;

    try {
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
                         verbose_logging=$7, updated_at=NOW()
                     WHERE employee_id IS NULL`,
                    [screenshot_min_minutes, screenshot_max_minutes, screenshot_count,
                     upload_interval_minutes, idle_threshold_seconds, force_logout,
                     verbose_logging]
                );
            } else {
                await pool.query(
                    `INSERT INTO employee_configs
                     (employee_id, screenshot_min_minutes, screenshot_max_minutes,
                      screenshot_count, upload_interval_minutes, idle_threshold_seconds,
                      force_logout, verbose_logging, updated_at)
                     VALUES (NULL,$1,$2,$3,$4,$5,$6,$7,NOW())`,
                    [screenshot_min_minutes, screenshot_max_minutes, screenshot_count,
                     upload_interval_minutes, idle_threshold_seconds, force_logout,
                     verbose_logging]
                );
            }
        } else {
            await pool.query(
                `INSERT INTO employee_configs
                 (employee_id, screenshot_min_minutes, screenshot_max_minutes,
                  screenshot_count, upload_interval_minutes, idle_threshold_seconds,
                  force_logout, verbose_logging, updated_at)
                 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,NOW())
                 ON CONFLICT (employee_id) DO UPDATE SET
                     screenshot_min_minutes = EXCLUDED.screenshot_min_minutes,
                     screenshot_max_minutes = EXCLUDED.screenshot_max_minutes,
                     screenshot_count = EXCLUDED.screenshot_count,
                     upload_interval_minutes = EXCLUDED.upload_interval_minutes,
                     idle_threshold_seconds = EXCLUDED.idle_threshold_seconds,
                     force_logout = EXCLUDED.force_logout,
                     verbose_logging = EXCLUDED.verbose_logging,
                     updated_at = NOW()`,
                [employee_id, screenshot_min_minutes, screenshot_max_minutes,
                 screenshot_count, upload_interval_minutes, idle_threshold_seconds,
                 force_logout, verbose_logging]
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
    const { employee_id, date, page = 1 } = req.query;
    const limit  = 50;
    const offset = (page - 1) * limit;

    const conditions = [];
    const values     = [];
    let   idx        = 1;

    if (employee_id) { conditions.push(`employee_id = $${idx++}`); values.push(employee_id); }
    if (date)        { conditions.push(`DATE(created_at) = $${idx++}`); values.push(date); }

    const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";

    try {
        const noiseWhere = where
            ? `${where} AND activity NOT LIKE '%ConfigSyncManager%' AND activity NOT LIKE '%SchedulerService%' AND activity NOT LIKE '%SYNC SAVE%'`
            : `WHERE activity NOT LIKE '%ConfigSyncManager%' AND activity NOT LIKE '%SchedulerService%' AND activity NOT LIKE '%SYNC SAVE%'`;
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
        const recent = await pool.query(
            `SELECT created_at, activity
             FROM activity_logs
             WHERE employee_id = $1
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
             WHERE employee_id = $1 AND logout_time IS NULL LIMIT 1`,
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

        for (let i = 0; i < events.rows.length - 1; i++) {
            const cur  = events.rows[i];
            const next = events.rows[i + 1];
            const state = normalizeState(cur.activity);
            if (!state) continue;

            const dt = new Date(next.created_at).getTime() - new Date(cur.created_at).getTime();
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
                latestEventTime = new Date(events.rows[i].created_at).getTime();
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
