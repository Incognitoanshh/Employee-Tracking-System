const pool = require("../config/db");

const DEFAULT_CONFIG = {
    screenshot_min_minutes:  3,
    screenshot_max_minutes:  10,
    screenshot_count:        3,
    upload_interval_minutes: 60,
    idle_threshold_seconds:  60,
    force_logout:            false,
    verbose_logging:         false,
};

exports.syncConfig = async (req, res) => {
    const { employee_id, device_id } = req.body;
    const token_employee_id = req.employee?.employee_id;

    if (!employee_id) {
        return res.status(400).json({ success: false, message: "employee_id is required" });
    }

    if (token_employee_id && token_employee_id !== employee_id) {
        return res.status(403).json({ success: false, message: "employee_id mismatch with token" });
    }

    try {
        let configRow = null;

        try {
            const empResult = await pool.query(
                `SELECT * FROM employee_configs WHERE employee_id = $1 ORDER BY updated_at DESC LIMIT 1`,
                [employee_id]
            );
            configRow = empResult.rows[0] || null;
        } catch (_) {}

        if (!configRow) {
            try {
                const globalResult = await pool.query(
                    `SELECT * FROM employee_configs WHERE employee_id IS NULL ORDER BY updated_at DESC LIMIT 1`
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
            verbose_logging:         configRow?.verbose_logging         ?? DEFAULT_CONFIG.verbose_logging,
        };

        // FIX: force_logout flag reset karo after sending
        if (config.force_logout) {
            await pool.query(
                `UPDATE employee_configs SET force_logout = false, updated_at = NOW() WHERE employee_id = $1`,
                [employee_id]
            );
        }

        // Shift timings come from employee_configs.shift_start/shift_end (HH:MM in IST).
        let shift = null;
        try {
            const shiftResult = await pool.query(
                `SELECT shift_start, shift_end FROM employee_configs WHERE employee_id = $1 LIMIT 1`,
                [employee_id]
            );
            const s = shiftResult.rows[0];
            if (s?.shift_start && s?.shift_end) {
                const today = new Date().toISOString().split("T")[0];
                shift = {
                    start_ist: `${today}T${String(s.shift_start).substring(0, 5)}:00+05:30`,
                    end_ist:   `${today}T${String(s.shift_end).substring(0, 5)}:00+05:30`,
                };
            }
        } catch (e) {}


        return res.status(200).json({
            success: true,
            config: {
                ...config,
                ...(shift ? { shift } : {}),
            }
        });

    } catch (error) {
        return res.status(500).json({ success: false, message: "Server error", error: error.message });
    }
};
