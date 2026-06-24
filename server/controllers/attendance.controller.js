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

        // DEBUG: Log incoming request
        console.log("[SERVER] loginAttendance received:", { employee_id, login_time });
        console.log("[SERVER] req.body:", JSON.stringify(req.body));
        console.log("[SERVER] req.headers:", JSON.stringify(req.headers));

        if (!employee_id) {
            console.log("[SERVER] ERROR: employee_id is undefined!");
            return res.status(400).json({ success: false, error: "employee_id required" });
        }

        // FIX: Close any existing open attendance records for this employee
        // Note: total_hours is interval type in PostgreSQL, cannot set to string.
        // Use NULL for forced close.
        await pool.query(
            `
            UPDATE attendance
            SET logout_time = login_time,
                total_hours = NULL
            WHERE employee_id = $1 AND logout_time IS NULL
            `,
            [employee_id]
        );

        console.log("[SERVER] Closed existing attendance records for:", employee_id);

        const result = await pool.query(
            `
            INSERT INTO attendance
            (
                employee_id,
                login_time
            )
            VALUES ($1, $2)
            RETURNING id
            `,
            [
                employee_id,
                login_time
            ]
        );

        console.log("[SERVER] Inserted attendance, id:", result.rows[0].id);

        res.json({
            success: true,
            id: result.rows[0].id
        });

    } catch (error) {
        console.log("[SERVER] loginAttendance ERROR:", error.message);
        console.log("[SERVER] Stack:", error.stack);

        res.status(500).json({
            success: false,
            error: error.message
        });

    }
};

exports.logoutAttendance = async (req, res) => {
    try {

        const {
            employee_id,
            logout_time,
            total_hours
        } = req.body;

        await pool.query(
            `
            UPDATE attendance
            SET
                logout_time = $1,
                total_hours = $2
            WHERE id = (
                SELECT id
                FROM attendance
                WHERE employee_id = $3
                ORDER BY id DESC
                LIMIT 1
            )
            `,
            [
                logout_time,
                total_hours,
                employee_id
            ]
        );

        res.json({
            success: true
        });

    } catch (error) {

        res.status(500).json({
            success: false,
            error: error.message
        });

    }
};