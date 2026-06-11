const pool = require("../config/db");

exports.getEmployees = async (req, res) => {
    try {
        const result = await pool.query(
            `SELECT employee_id, username, role
            FROM employees
            ORDER BY employee_id ASC`
        );
        return res.json({ success: true, data: result.rows });
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
    } = req.body;

    try {

        // GLOBAL CONFIG
        if (employee_id === null) {

            const existing = await pool.query(
                `SELECT id
                 FROM employee_configs
                 WHERE employee_id IS NULL
                 LIMIT 1`
            );

            if (existing.rows.length > 0) {

                await pool.query(
                    `
                    UPDATE employee_configs
                    SET
                        screenshot_min_minutes = $1,
                        screenshot_max_minutes = $2,
                        screenshot_count = $3,
                        upload_interval_minutes = $4,
                        idle_threshold_seconds = $5,
                        force_logout = $6,
                        updated_at = NOW()
                    WHERE employee_id IS NULL
                    `,
                    [
                        screenshot_min_minutes,
                        screenshot_max_minutes,
                        screenshot_count,
                        upload_interval_minutes,
                        idle_threshold_seconds,
                        force_logout,
                    ]
                );

            } else {

                await pool.query(
                    `
                    INSERT INTO employee_configs
                    (
                        employee_id,
                        screenshot_min_minutes,
                        screenshot_max_minutes,
                        screenshot_count,
                        upload_interval_minutes,
                        idle_threshold_seconds,
                        force_logout,
                        updated_at
                    )
                    VALUES
                    (
                        NULL,
                        $1,$2,$3,$4,$5,$6,
                        NOW()
                    )
                    `,
                    [
                        screenshot_min_minutes,
                        screenshot_max_minutes,
                        screenshot_count,
                        upload_interval_minutes,
                        idle_threshold_seconds,
                        force_logout,
                    ]
                );
            }

        } else {

            // EMPLOYEE SPECIFIC CONFIG
            await pool.query(
                `
                INSERT INTO employee_configs
                (
                    employee_id,
                    screenshot_min_minutes,
                    screenshot_max_minutes,
                    screenshot_count,
                    upload_interval_minutes,
                    idle_threshold_seconds,
                    force_logout,
                    updated_at
                )
                VALUES
                (
                    $1,$2,$3,$4,$5,$6,$7,
                    NOW()
                )
                ON CONFLICT (employee_id)
                DO UPDATE SET
                    screenshot_min_minutes = EXCLUDED.screenshot_min_minutes,
                    screenshot_max_minutes = EXCLUDED.screenshot_max_minutes,
                    screenshot_count = EXCLUDED.screenshot_count,
                    upload_interval_minutes = EXCLUDED.upload_interval_minutes,
                    idle_threshold_seconds = EXCLUDED.idle_threshold_seconds,
                    force_logout = EXCLUDED.force_logout,
                    updated_at = NOW()
                `,
                [
                    employee_id,
                    screenshot_min_minutes,
                    screenshot_max_minutes,
                    screenshot_count,
                    upload_interval_minutes,
                    idle_threshold_seconds,
                    force_logout,
                ]
            );
        }

        console.log(
            `[ADMIN CONFIG SAVED] employee_id=${employee_id ?? "global"}`
        );

        return res.json({
            success: true,
            message: "Config saved."
        });

    } catch (err) {

        console.error("SAVE CONFIG ERROR:", err);

        return res.status(500).json({
            success: false,
            error: err.message
        });
    }
};

exports.forceLogout = async (req, res) => {
    const { employee_id } = req.body;

    if (!employee_id) {
        return res.status(400).json({ success: false, message: "employee_id required" });
    }

    try {
        // force_logout = true set karo — agla config sync pe client logout ho jaayega
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
    if (date)        { conditions.push(`DATE(created_at) = $${idx++}`); values.push(date); }

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
            `SELECT COUNT(*) FROM screenshots ${where}`,
            values
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
        const result = await pool.query(
            `SELECT id, employee_id, activity, created_at
             FROM activity_logs
             ${where}
             ORDER BY created_at DESC
             LIMIT $${idx} OFFSET $${idx + 1}`,
            [...values, limit, offset]
        );

        const countResult = await pool.query(
            `SELECT COUNT(*) FROM activity_logs ${where}`,
            values
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
