const pool = require("../config/db");

exports.getAttendance = async (req, res) => {
    try {
        let { employee_id, page = 1 } = req.query;
        const limit  = 50;
        const offset = (page - 1) * limit;

        // SECURITY: non-admin apna data hi dekh sakte hain
        if (req.employee?.role !== "admin") {
            employee_id = req.employee?.employee_id;
        }

        const conditions = [];
        const values     = [];
        let   idx        = 1;

        if (employee_id) {
            conditions.push(`employee_id = $${idx++}`);
            values.push(employee_id);
        }

        const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";

        const result = await pool.query(
            `SELECT id, employee_id, login_time, logout_time, total_hours
             FROM attendance ${where}
             ORDER BY id DESC
             LIMIT $${idx} OFFSET $${idx + 1}`,
            [...values, limit, offset]
        );

        const countResult = await pool.query(
            `SELECT COUNT(*) FROM attendance ${where}`, values
        );

        return res.json({
            success: true,
            data:    result.rows,
            total:   Number(countResult.rows[0].count),
            page:    Number(page),
        });

    } catch (error) {
        return res.status(500).json({ success: false, error: error.message });
    }
};

exports.loginAttendance = async (req, res) => {
    try {
        let { employee_id, login_time } = req.body;

        if (req.employee?.role !== "admin") {
            employee_id = req.employee?.employee_id;
        }

        if (!employee_id) {
            return res.status(400).json({ success: false, error: "employee_id required" });
        }

        // Close any open sessions
        await pool.query(
            `UPDATE attendance SET logout_time = NOW(), total_hours = NULL
             WHERE employee_id = $1 AND logout_time IS NULL`,
            [employee_id]
        );

        // BUG FIX: login_time ko UTC mein store karo
        // Client IST string bhejta tha jaise "2026-06-28 15:30:00"
        // PostgreSQL bina timezone ke store karta tha — inconsistent tha
        // Ab hum seedha NOW() use karte hain jo UTC mein hai
        const result = await pool.query(
            `INSERT INTO attendance (employee_id, login_time) VALUES ($1, NOW()) RETURNING id`,
            [employee_id]
        );

        res.json({ success: true, id: result.rows[0].id });

    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
};

exports.logoutAttendance = async (req, res) => {
    try {
        let { employee_id, total_hours } = req.body;

        if (req.employee?.role !== "admin") {
            employee_id = req.employee?.employee_id;
        }

        if (!employee_id) {
            return res.status(400).json({ success: false, error: "employee_id required" });
        }

        // Validate total_hours
        let interval_value = null;
        if (total_hours && typeof total_hours === "string" && total_hours.trim().length > 0) {
            const validInterval = /^\d+\s*(hour[s]?\s*)?\d*\s*(minute[s]?)?$|^\d+\s*minutes?$|^\d+\s*hours?$/i;
            if (validInterval.test(total_hours.trim())) {
                interval_value = total_hours.trim();
            }
        }

        // BUG FIX: logout_time bhi NOW() se UTC mein store karo
        const result = await pool.query(
            `UPDATE attendance
             SET logout_time = NOW(), total_hours = $1::interval
             WHERE id = (
                 SELECT id FROM attendance
                 WHERE employee_id = $2 AND logout_time IS NULL
                 ORDER BY id DESC LIMIT 1
             )
             RETURNING id`,
            [interval_value, employee_id]
        );

        if (result.rows.length === 0) {
            return res.status(404).json({ success: false, error: "No open attendance session found" });
        }

        res.json({ success: true });

    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
};
