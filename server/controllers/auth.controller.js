const pool = require("../config/db");
const jwt = require("jsonwebtoken");
const bcrypt = require("bcryptjs");
const { validatePassword } = require("../utils/password_policy");

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
    const { username, password } = req.body || {};

    if (!username || !password) {
        return res.status(400).json({ success: false, message: "username and password are required" });
    }

    try {
        // Matched without regard to case. The super admin registered as
        // "Amazeinternet" could not sign in by typing "amazeinternet": the
        // password was right and the only feedback was "Invalid credentials",
        // which reads as a wrong password and sends people round in circles.
        //
        // Safe only because employees_username_lower_idx makes two accounts
        // differing by case impossible — otherwise this would match more than
        // one row and let a lookalike account take over a real one.
        //
        // Uses the same LOWER(username) expression as that index so the
        // lookup stays indexed rather than scanning the table.
        const result = await pool.query(
            "SELECT * FROM employees WHERE LOWER(username) = LOWER($1)",
            [String(username).trim()]
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
            // The client shows the change-password screen instead of the
            // panel while this is true, so an admin-issued temporary
            // password cannot become someone's permanent one.
            must_change_password: employee.must_change_password === true,
            // Naye employee panel ke header ke liye — naam na ho to username.
            full_name:   employee.full_name || employee.username,
            designation: employee.designation
                || (employee.role === "super_admin" ? "Administrator"
                    : employee.role === "admin" ? "Manager" : "Employee"),
            token,
            shift_start: shiftStart,
            shift_end:   shiftEnd,
            config: {
                screenshot_min_minutes:  config.screenshot_min_minutes  || 3,
                screenshot_max_minutes:  config.screenshot_max_minutes  || 10,
                screenshots_per_day:        config.screenshots_per_day        || 10,
                upload_interval_minutes: config.upload_interval_minutes || 60,
                idle_threshold_seconds:  config.idle_threshold_seconds  || 60,
                verbose_logging:         config.verbose_logging         || false,
            }
        });

    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Server error" });
    }
};

/**
 * An employee changes their own password.
 *
 * Requires the current password even though the caller already holds a valid
 * token: a token left behind on a machine someone else can reach must not be
 * enough to lock the real owner out of their own account.
 *
 * Every other device is signed out. `active_sessions` holds one token per
 * employee, so writing the new token here is what does it — anything
 * presenting the old one fails `verifyToken`'s mismatch check. The caller
 * gets that new token back so the device they are sitting at stays working;
 * without it, changing your password would immediately log you out of the
 * app you changed it in.
 */
exports.changePassword = async (req, res) => {
    const { current_password, new_password } = req.body || {};
    const employeeId = req.employee?.employee_id;

    if (!employeeId) {
        return res.status(401).json({ success: false, message: "Not authenticated" });
    }
    if (!current_password || !new_password) {
        return res.status(400).json({
            success: false,
            message: "current_password and new_password are required",
        });
    }

    try {
        const result = await pool.query(
            `SELECT employee_id, username, password, role FROM employees WHERE employee_id = $1`,
            [employeeId]
        );
        if (result.rows.length === 0) {
            return res.status(404).json({ success: false, message: "Account not found" });
        }
        const employee = result.rows[0];

        const isMatch = await bcrypt.compare(current_password, employee.password);
        if (!isMatch) {
            return res.status(401).json({
                success: false,
                message: "Current password is incorrect",
            });
        }

        const problem = validatePassword(new_password, {
            username:   employee.username,
            employeeId: employee.employee_id,
        });
        if (problem) {
            return res.status(400).json({ success: false, message: problem });
        }

        // Reusing the current password would leave `must_change_password`
        // cleared without anything actually changing, which defeats a reset.
        if (await bcrypt.compare(new_password, employee.password)) {
            return res.status(400).json({
                success: false,
                message: "New password must be different from the current one",
            });
        }

        const hashed = await bcrypt.hash(new_password, 10);
        await pool.query(
            `UPDATE employees
                SET password = $1, must_change_password = false, password_changed_at = NOW()
              WHERE employee_id = $2`,
            [hashed, employeeId]
        );

        const token = jwt.sign(
            { employee_id: employee.employee_id, role: employee.role },
            process.env.JWT_SECRET,
            { expiresIn: "24h" }
        );
        await pool.query(
            `INSERT INTO active_sessions (employee_id, token, login_time)
             VALUES ($1, $2, NOW())
             ON CONFLICT (employee_id) DO UPDATE SET token = $2, login_time = NOW()`,
            [employeeId, token]
        );

        await pool.query(
            `INSERT INTO activity_logs (employee_id, activity) VALUES ($1, $2)`,
            [employeeId, "PASSWORD CHANGED : by the account owner"]
        ).catch(() => {});

        return res.json({
            success: true,
            message: "Password changed. Other devices have been signed out.",
            token,
        });

    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};
