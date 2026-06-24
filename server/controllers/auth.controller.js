const pool = require("../config/db");
const jwt = require("jsonwebtoken");
const bcrypt = require("bcryptjs");  // ← ADD THIS

exports.login = async (req, res) => {

    console.log("LOGIN HIT");
    console.log("BODY RECEIVED:", req.body);

    const { username, password } = req.body;

    if (!username || !password) {
        return res.status(400).json({
            success: false,
            message: "username and password are required"
        });
    }

    try {

        const result = await pool.query(
            "SELECT * FROM employees WHERE username = $1",
            [username]
        );

        if (result.rows.length === 0) {
            return res.status(401).json({
                success: false,
                message: "Invalid credentials"
            });
        }

        const employee = result.rows[0];

        // ✅ FIX: bcrypt se compare karo, plaintext nahi
        const isMatch = await bcrypt.compare(password, employee.password);
        if (!isMatch) {
            return res.status(401).json({
                success: false,
                message: "Invalid credentials"
            });
        }
        // Purane open sessions band karo — multi device prevention
        await pool.query(
            `UPDATE attendance SET logout_time = NOW()
             WHERE employee_id = $1 AND logout_time IS NULL`,
            [employee.employee_id]
        );

        const token = jwt.sign(
            {
                employee_id: employee.employee_id,
                role: employee.role
            },
            process.env.JWT_SECRET,
            { expiresIn: "8h" }
        );
        console.log("LOGIN RESPONSE:", {
            success: true,
            employee_id: employee.employee_id,
            role: employee.role,
            token: "TOKEN_HIDDEN"
        });
        await pool.query(
            `UPDATE employee_configs SET force_logout = false WHERE employee_id = $1`,
            [employee.employee_id]
        );
        // Naya active session record karo
        await pool.query(
            `INSERT INTO active_sessions (employee_id, token, login_time)
             VALUES ($1, $2, NOW())
             ON CONFLICT (employee_id) DO UPDATE
             SET token = $2, login_time = NOW()`,
            [employee.employee_id, token]
        );
        // Employee config fetch karo shift ke liye
        const configResult = await pool.query(
            `SELECT * FROM employee_configs WHERE employee_id = $1`,
            [employee.employee_id]
        );
        const globalConfig = await pool.query(
            `SELECT * FROM employee_configs WHERE employee_id IS NULL LIMIT 1`
        );
        const config = configResult.rows[0] || globalConfig.rows[0] || {};

        return res.status(200).json({
            success: true,
            employee_id: employee.employee_id,
            role: employee.role,
            token,
            shift_start: config.shift_start_ist || "09:00",
            shift_end: config.shift_end_ist || "18:00",
            config: {
                screenshot_min_minutes: config.screenshot_min_minutes || 3,
                screenshot_max_minutes: config.screenshot_max_minutes || 10,
                screenshot_count: config.screenshot_count || 3,
                upload_interval_minutes: config.upload_interval_minutes || 60,
                idle_threshold_seconds: config.idle_threshold_seconds || 60,
                verbose_logging: config.verbose_logging || false
            }
        });

    } catch (error) {
        console.log("LOGIN ERROR:", error);
        return res.status(500).json({
            success: false,
            message: "Server error"
        });
    }

};
