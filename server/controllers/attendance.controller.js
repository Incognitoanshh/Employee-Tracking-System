const pool = require("../config/db");

exports.getAttendance = async (req, res) => {
    try {
        const result = await pool.query(`
            SELECT
                id,
                employee_id,
                login_time,
                logout_time,
                total_hours
            FROM attendance
            ORDER BY id DESC
        `);

        return res.json({
            success: true,
            data: result.rows
        });

    } catch (error) {
        return res.status(500).json({
            success: false,
            error: error.message
        });
    }
};

exports.loginAttendance = async (req, res) => {
    try {
        const { employee_id, login_time } = req.body;

        console.log("[SERVER] loginAttendance received:", { employee_id, login_time });

        if (!employee_id) {
            return res.status(400).json({ success: false, error: "employee_id required" });
        }

        // Close any existing open attendance records
        await pool.query(
            `UPDATE attendance
             SET logout_time = login_time,
                 total_hours = NULL
             WHERE employee_id = $1 AND logout_time IS NULL`,
            [employee_id]
        );

        const result = await pool.query(
            `INSERT INTO attendance (employee_id, login_time)
             VALUES ($1, $2)
             RETURNING id`,
            [employee_id, login_time]
        );

        res.json({
            success: true,
            id: result.rows[0].id
        });

    } catch (error) {
        console.log("[SERVER] loginAttendance ERROR:", error.message);
        res.status(500).json({
            success: false,
            error: error.message
        });
    }
};

exports.logoutAttendance = async (req, res) => {
    try {
        const { employee_id, logout_time, total_hours } = req.body;

        // BUG FIX: PostgreSQL `total_hours` column is INTERVAL type.
        // Client bhej raha tha string like "1 hour 30 minutes" jo valid PostgreSQL
        // interval string hai — lekin NULL bheja to error aata tha.
        // Ab hum string ko direct pass karte hain (PostgreSQL automatically parse karta hai
        // "1 hour 30 minutes", "2 hours", "45 minutes" etc.).
        // Agar total_hours nahi aaya to NULL set karo gracefully.

        let interval_value = null;
        if (total_hours && typeof total_hours === "string" && total_hours.trim().length > 0) {
            // PostgreSQL accepts these formats natively from node-postgres
            interval_value = total_hours.trim();
        }

        await pool.query(
            `UPDATE attendance
             SET logout_time = $1,
                 total_hours = $2::interval
             WHERE id = (
                 SELECT id FROM attendance
                 WHERE employee_id = $3
                 ORDER BY id DESC
                 LIMIT 1
             )`,
            [logout_time, interval_value, employee_id]
        );

        res.json({ success: true });

    } catch (error) {
        console.log("[LOGOUT ATTENDANCE ERROR]", error.message);
        res.status(500).json({
            success: false,
            error: error.message
        });
    }
};
