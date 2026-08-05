const pool = require("../config/db");

const DEFAULT_CONFIG = {
    screenshot_min_minutes:  3,
    screenshot_max_minutes:  10,
    screenshots_per_day:     10,
    upload_interval_minutes: 60,
    idle_threshold_seconds:  60,
    force_logout:            false,
    verbose_logging:         false,
};

exports.syncConfig = async (req, res) => {
    const { employee_id, device_id } = req.body || {};
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
            screenshots_per_day:        configRow?.screenshots_per_day        ?? DEFAULT_CONFIG.screenshots_per_day,
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
            // BUG FIX: ye query SIRF employee-specific row dekhti thi. Jis
            // employee ka apna koi config row nahi hai (yaani jo default pe
            // chal raha hai — normal case), uske liye `shift` hamesha null
            // aata tha. Client ka SchedulerService phir "shift times not
            // found" pe gir kar "login se 8 ghante" wala window use karta —
            // yaani admin ka set kiya hua GLOBAL shift kabhi apply hi nahi
            // hota tha, aur screenshots asli shift ke bahar schedule ho
            // jaate the. Baaki saare config fields pehle se hi global row pe
            // fall back karte hain (upar `configRow`) — shift ko bhi wahi
            // consistent behaviour chahiye.
            let shiftResult = await pool.query(
                `SELECT shift_start, shift_end FROM employee_configs WHERE employee_id = $1 LIMIT 1`,
                [employee_id]
            );

            if (!shiftResult.rows[0]?.shift_start || !shiftResult.rows[0]?.shift_end) {
                shiftResult = await pool.query(
                    `SELECT shift_start, shift_end FROM employee_configs
                     WHERE employee_id IS NULL LIMIT 1`
                );
            }

            const s = shiftResult.rows[0];
            if (s?.shift_start && s?.shift_end) {
                const today = new Date().toISOString().split("T")[0];
                shift = {
                    start_ist: `${today}T${String(s.shift_start).substring(0, 5)}:00+05:30`,
                    end_ist:   `${today}T${String(s.shift_end).substring(0, 5)}:00+05:30`,
                };
            }
        } catch (e) {
            // Pehle ye catch bilkul khaali tha. Agar shift query kabhi fail
            // hoti (DB blip, statement_timeout), shift chup-chaap null ho
            // jaata aur client "login se 8 ghante" wale fallback window pe
            // chala jaata — screenshots galat time pe, aur logs me iska koi
            // nishaan tak nahi. Ab kam se kam trace to milega.
            console.error(`[CONFIG SYNC] shift lookup failed for ${employee_id}:`, e.message);
        }


        return res.status(200).json({
            success: true,
            config: {
                ...config,
                ...(shift ? { shift } : {}),
            }
        });

    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};
