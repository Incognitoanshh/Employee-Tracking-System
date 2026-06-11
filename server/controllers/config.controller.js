const pool = require("../config/db");

const DEFAULT_CONFIG = {
    screenshot_min_minutes:  3,
    screenshot_max_minutes:  10,
    screenshot_count:        3,   // ← naya field: shift mein kitne screenshots
    upload_interval_minutes: 60,
    idle_threshold_seconds:  60,
    force_logout:            false,
};

exports.syncConfig = async (req, res) => {

    console.log("CONFIG SYNC HIT");
    console.log("BODY:", req.body);

    const { employee_id, device_id } = req.body;
    const token_employee_id = req.employee?.employee_id;

    if (!employee_id) {
        return res.status(400).json({
            success: false,
            message: "employee_id is required"
        });
    }

    if (token_employee_id && token_employee_id !== employee_id) {
        return res.status(403).json({
            success: false,
            message: "employee_id mismatch with token"
        });
    }

    try {

        let configRow = null;

        // Pehle employee-specific config dhundo
        try {
            const empResult = await pool.query(
                `SELECT * FROM employee_configs
                 WHERE employee_id = $1
                 ORDER BY updated_at DESC LIMIT 1`,
                [employee_id]
            );
            configRow = empResult.rows[0] || null;
        } catch (_) {}

        // Nahi mila to global default lo
        if (!configRow) {
            try {
                const globalResult = await pool.query(
                    `SELECT * FROM employee_configs
                     WHERE employee_id IS NULL
                     ORDER BY updated_at DESC LIMIT 1`
                );
                configRow = globalResult.rows[0] || null;
            } catch (_) {}
        }

        const config = {
            screenshot_min_minutes:  configRow?.screenshot_min_minutes  ?? DEFAULT_CONFIG.screenshot_min_minutes,
            screenshot_max_minutes:  configRow?.screenshot_max_minutes  ?? DEFAULT_CONFIG.screenshot_max_minutes,
            screenshot_count:        configRow?.screenshot_count        ?? DEFAULT_CONFIG.screenshot_count,
            upload_interval_minutes: configRow?.upload_interval_minutes ?? DEFAULT_CONFIG.upload_interval_minutes,
            idle_threshold_seconds:  configRow?.idle_threshold_seconds  ?? DEFAULT_CONFIG.idle_threshold_seconds,
            force_logout:            configRow?.force_logout            ?? DEFAULT_CONFIG.force_logout,
        };
        if (config.force_logout) {
            await pool.query(
                `
                UPDATE employee_configs
                SET force_logout = false,
                    updated_at = NOW()
                WHERE employee_id IS NULL
                `
            );
        console.log("FORCE LOGOUT FLAG RESET");
        }   

        // Shift timings
        let shift = null;
        try {
            const shiftResult = await pool.query(
                `SELECT start_ist, end_ist FROM shifts
                 WHERE employee_id = $1
                   AND shift_date  = CURRENT_DATE
                 LIMIT 1`,
                [employee_id]
            );

            if (shiftResult.rows.length > 0) {
                const s     = shiftResult.rows[0];
                const today = new Date().toISOString().split("T")[0];
                shift = {
                    start_ist: `${today}T${s.start_ist}:00+05:30`,
                    end_ist:   `${today}T${s.end_ist}:00+05:30`,
                };
            }
        } catch (_) {}

        const responsePayload = {
            success: true,
            config: {
                ...config,
                ...(shift ? { shift } : {}),
            }
        };

        console.log("CONFIG SYNC RESPONSE:", JSON.stringify(responsePayload));
        return res.status(200).json(responsePayload);

    } catch (error) {
        console.error("CONFIG SYNC ERROR:", error);
        return res.status(500).json({
            success:  false,
            message:  "Server error during config sync",
            error:    error.message
        });
    }
};
