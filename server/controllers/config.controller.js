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
    // ✅ employee_id token se lo — body pe trust mat karo
    const employee_id = req.user?.employee_id;

    if (!employee_id) {
        return res.status(401).json({
            success: false,
            message: "Unauthorized",
        });
    }

    const { device_id } = req.body; // device_id sirf logging ke liye

    try {
        // Update active_sessions last_ping
        await pool.query(
            `UPDATE active_sessions
             SET last_ping = NOW()
             WHERE employee_id = $1`,
            [employee_id]
        );

        // ── Config fetch ──────────────────────────────────────
        // Employee-specific config
        const empResult = await pool.query(
            `SELECT * FROM employee_configs
             WHERE employee_id = $1
             ORDER BY updated_at DESC LIMIT 1`,
            [employee_id]
        );

        let configRow = empResult.rows[0] || null;

        // Global fallback
        if (!configRow) {
            const globalResult = await pool.query(
                `SELECT * FROM employee_configs
                 WHERE employee_id IS NULL
                 ORDER BY updated_at DESC LIMIT 1`
            );
            configRow = globalResult.rows[0] || null;
        }

        const config = {
            screenshot_min_minutes:  configRow?.screenshot_min_minutes
                ?? DEFAULT_CONFIG.screenshot_min_minutes,
            screenshot_max_minutes:  configRow?.screenshot_max_minutes
                ?? DEFAULT_CONFIG.screenshot_max_minutes,
            screenshot_count:        configRow?.screenshot_count
                ?? DEFAULT_CONFIG.screenshot_count,
            upload_interval_minutes: configRow?.upload_interval_minutes
                ?? DEFAULT_CONFIG.upload_interval_minutes,
            idle_threshold_seconds:  configRow?.idle_threshold_seconds
                ?? DEFAULT_CONFIG.idle_threshold_seconds,
            force_logout:            configRow?.force_logout
                ?? DEFAULT_CONFIG.force_logout,
            verbose_logging:         configRow?.verbose_logging
                ?? DEFAULT_CONFIG.verbose_logging,
        };

        // ── Force logout handling ─────────────────────────────
        if (config.force_logout) {
            // ✅ Active session bhi delete karo — token invalidate
            await pool.query(
                `DELETE FROM active_sessions WHERE employee_id = $1`,
                [employee_id]
            );

            // ✅ Flag reset
            await pool.query(
                `UPDATE employee_configs
                 SET force_logout = false, updated_at = NOW()
                 WHERE employee_id = $1`,
                [employee_id]
            );

            console.log(`[CONFIG SYNC] force_logout sent to ${employee_id}`);

            // Force logout response — shift nahi bhejte
            return res.status(200).json({
                success: true,
                config:  { force_logout: true },
            });
        }

        // ── Shift timings ─────────────────────────────────────
        let shift = null;
        try {
            const shiftResult = await pool.query(
                `SELECT shift_start_ist, shift_end_ist
                 FROM employee_configs
                 WHERE employee_id = $1
                 LIMIT 1`,
                [employee_id]
            );

            if (shiftResult.rows.length > 0) {
                const s     = shiftResult.rows[0];
                const today = new Date().toISOString().split("T")[0];

                // ✅ Format guard — "HH:MM" ya "HH:MM:SS" dono handle
                const fmt = (t) => {
                    if (!t) return null;
                    const parts = t.split(":");
                    return parts.length === 2
                        ? `${today}T${t}:00+05:30`
                        : `${today}T${t}+05:30`;
                };

                const start = fmt(s.shift_start_ist);
                const end   = fmt(s.shift_end_ist);

                if (start && end) {
                    shift = { start_ist: start, end_ist: end };
                }
            }
        } catch (e) {
            console.error("[CONFIG SYNC] shift fetch error:", e.message);
        }

        // ── Build response ────────────────────────────────────
        const responseConfig = {
            screenshot_min_minutes:  config.screenshot_min_minutes,
            screenshot_max_minutes:  config.screenshot_max_minutes,
            screenshot_count:        config.screenshot_count,
            upload_interval_minutes: config.upload_interval_minutes,
            idle_threshold_seconds:  config.idle_threshold_seconds,
            verbose_logging:         config.verbose_logging,
            force_logout:            false,
            ...(shift ? { shift } : {}),
        };

        console.log(
            `[CONFIG SYNC] ${employee_id} device=${device_id || "unknown"}`
        );

        return res.status(200).json({
            success: true,
            config:  responseConfig,
        });

    } catch (error) {
        console.error("[CONFIG SYNC ERROR]", error.message);
        return res.status(500).json({
            success: false,
            message: "Server error during config sync",
        });
    }
};