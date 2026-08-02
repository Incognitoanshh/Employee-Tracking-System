const pool = require("../config/db");
const jwt = require("jsonwebtoken");
const bcrypt = require("bcryptjs");

exports.logout = async (req, res) => {
    const authHeader = req.headers["authorization"];
    const token = authHeader && authHeader.split(" ")[1];
    if (!token) return res.status(400).json({ success: false, message: "No token provided" });

    // Best-effort decode (signature/expiry verify zaroori nahi — agar token
    // already expire ho chuka hai to bhi employee_id nikal ke uski
    // active_session clear kar do; logout ka intent already achieve hai).
    const decoded = jwt.decode(token);
    if (decoded && decoded.employee_id) {
        try {
            // token = NULL karo, row DELETE nahi — verifyToken middleware
            // sirf tab hi purane token ko reject karta hai jab active_sessions
            // row EXIST kare aur token mismatch ho. Row delete karne se check
            // hi skip ho jata (koi row hi nahi milta), purana token tab bhi
            // apni natural 24h expiry tak chalta rehta — security gap.
            await pool.query(
                `UPDATE active_sessions SET token = NULL WHERE employee_id = $1`,
                [decoded.employee_id]
            );
        } catch (dbError) {
            // DB issue — client-side session already clear ho chuki hogi,
            // logout request ko fail mat karo iske liye.
        }
    }

    return res.json({ success: true, message: "Logged out" });
};

exports.refresh = async (req, res) => {
    const authHeader = req.headers["authorization"];
    const token = authHeader && authHeader.split(" ")[1];
    if (!token) return res.status(401).json({ success: false, message: "No token provided" });

    try {
        const decoded = jwt.verify(token, process.env.JWT_SECRET);
        const newToken = jwt.sign(
            { employee_id: decoded.employee_id, role: decoded.role },
            process.env.JWT_SECRET,
            { expiresIn: "24h" }
        );
        await pool.query(
            `UPDATE active_sessions SET token = $1, login_time = NOW() WHERE employee_id = $2`,
            [newToken, decoded.employee_id]
        );
        return res.json({ success: true, token: newToken });
    } catch (error) {
        return res.status(403).json({ success: false, message: "Invalid or expired token" });
    }
};

exports.login = async (req, res) => {
    const { username, password } = req.body;

    if (!username || !password) {
        return res.status(400).json({ success: false, message: "username and password are required" });
    }

    try {
        const result = await pool.query(
            "SELECT * FROM employees WHERE username = $1",
            [username]
        );

        if (result.rows.length === 0) {
            return res.status(401).json({ success: false, message: "Invalid credentials" });
        }

        const employee = result.rows[0];

        const isMatch = await bcrypt.compare(password, employee.password);
        if (!isMatch) {
            return res.status(401).json({ success: false, message: "Invalid credentials" });
        }

        // FIX: Login pe attendance close NAHI karo — employee online status ke liye
        // open attendance record rehna chahiye. attendance/login endpoint
        // apna open session khud close karke naya banata hai.

        // FIX: 24h token expiry
        const token = jwt.sign(
            { employee_id: employee.employee_id, role: employee.role },
            process.env.JWT_SECRET,
            { expiresIn: "24h" }
        );

        // Reset force_logout on login
        await pool.query(
            `UPDATE employee_configs SET force_logout = false WHERE employee_id = $1`,
            [employee.employee_id]
        );

        // New active session
        await pool.query(
            `INSERT INTO active_sessions (employee_id, token, login_time)
             VALUES ($1, $2, NOW())
             ON CONFLICT (employee_id) DO UPDATE SET token = $2, login_time = NOW()`,
            [employee.employee_id, token]
        );

        // Employee config + shift fetch
        const configResult = await pool.query(
            `SELECT * FROM employee_configs WHERE employee_id = $1`,
            [employee.employee_id]
        );
        const globalConfig = await pool.query(
            `SELECT * FROM employee_configs WHERE employee_id IS NULL LIMIT 1`
        );
        const config = configResult.rows[0] || globalConfig.rows[0] || {};

        // shift_start/shift_end are stored in employee_configs and are HH:MM IST.
        const shiftStart = config.shift_start
            ? String(config.shift_start).substring(0, 5)
            : "09:00";
        const shiftEnd = config.shift_end
            ? String(config.shift_end).substring(0, 5)
            : "18:00";


        return res.status(200).json({
            success: true,
            employee_id: employee.employee_id,
            role: employee.role,
            token,
            shift_start: shiftStart,
            shift_end:   shiftEnd,
            config: {
                screenshot_min_minutes:  config.screenshot_min_minutes  || 3,
                screenshot_max_minutes:  config.screenshot_max_minutes  || 10,
                screenshot_count:        config.screenshot_count        || 3,
                upload_interval_minutes: config.upload_interval_minutes || 60,
                idle_threshold_seconds:  config.idle_threshold_seconds  || 60,
                verbose_logging:         config.verbose_logging         || false,
            }
        });

    } catch (error) {
        return res.status(500).json({ success: false, message: "Server error" });
    }
};
