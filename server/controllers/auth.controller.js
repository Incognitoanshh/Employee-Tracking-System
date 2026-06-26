const pool    = require("../config/db");
const jwt     = require("jsonwebtoken");
const bcrypt  = require("bcryptjs");

// ── Startup guard ─────────────────────────────────────────────
if (!process.env.JWT_SECRET) {
    console.error("❌ FATAL: JWT_SECRET not set in environment");
    process.exit(1);
}

exports.login = async (req, res) => {
    const { username, password } = req.body;

    // ✅ Password log nahi karo
    console.log("LOGIN attempt:", username);

    if (!username || !password) {
        return res.status(400).json({
            success: false,
            message: "Username and password are required",
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
                message: "Invalid credentials",
            });
        }

        const employee = result.rows[0];

        const isMatch = await bcrypt.compare(password, employee.password);
        if (!isMatch) {
            return res.status(401).json({
                success: false,
                message: "Invalid credentials",
            });
        }

        // ✅ Token generate
        const token = jwt.sign(
            {
                employee_id: employee.employee_id,
                role:        employee.role,
            },
            process.env.JWT_SECRET,
            { expiresIn: "8h" }
        );

        // ✅ Active session upsert — token plaintext nahi
        await pool.query(
            `INSERT INTO active_sessions (employee_id, token, login_time, last_ping)
             VALUES ($1, $2, NOW(), NOW())
             ON CONFLICT (employee_id)
             DO UPDATE SET token = $2, login_time = NOW(), last_ping = NOW()`,
            [employee.employee_id, token]
        );

        // ✅ force_logout reset
        await pool.query(
            `UPDATE employee_configs
             SET force_logout = false
             WHERE employee_id = $1`,
            [employee.employee_id]
        );

        // Config fetch — employee specific, fallback to global
        const configResult = await pool.query(
            `SELECT * FROM employee_configs
             WHERE employee_id = $1`,
            [employee.employee_id]
        );
        const globalResult = await pool.query(
            `SELECT * FROM employee_configs
             WHERE employee_id IS NULL
             LIMIT 1`
        );
        const cfg = configResult.rows[0] || globalResult.rows[0] || {};

        console.log("LOGIN SUCCESS:", employee.employee_id, "role:", employee.role);

        const { logAudit } = require("../utils/audit");
        await logAudit(employee.employee_id, "Employee Login");

        // ✅ Spec format — employee object ke andar
        return res.status(200).json({
            success: true,
            token,
            role: employee.role,
            employee: {
                employee_id: employee.employee_id,
                full_name:   employee.full_name || employee.username,
            },
            shift: {
                start_ist: cfg.shift_start_ist || null,
                end_ist:   cfg.shift_end_ist   || null,
            },
            config: {
                screenshot_min_minutes:  cfg.screenshot_min_minutes  || 3,
                screenshot_max_minutes:  cfg.screenshot_max_minutes  || 10,
                screenshot_count:        cfg.screenshot_count        || 3,
                upload_interval_minutes: cfg.upload_interval_minutes || 60,
                idle_threshold_seconds:  cfg.idle_threshold_seconds  || 60,
                verbose_logging:         cfg.verbose_logging         || false,
            },
        });

    } catch (error) {
        console.error("LOGIN ERROR:", error.message);
        return res.status(500).json({
            success: false,
            message: "Server error",
        });
    }
};

exports.logout = async (req, res) => {
    // req.user verifyToken middleware se aata hai
    const employee_id = req.user?.employee_id;

    try {
        if (employee_id) {
            // ✅ Active session remove karo
            await pool.query(
                `DELETE FROM active_sessions
                 WHERE employee_id = $1`,
                [employee_id]
            );
            console.log("LOGOUT:", employee_id);
            const { logAudit } = require("../utils/audit");
            await logAudit(employee_id, "Logout");
        }

        return res.status(200).json({
            success: true,
            message: "Session closed",
        });

    } catch (error) {
        console.error("LOGOUT ERROR:", error.message);
        return res.status(500).json({
            success: false,
            message: "Server error",
        });
    }
};

exports.heartbeat = async (req, res) => {
    const employee_id = req.user?.employee_id;

    if (!employee_id) {
        return res.status(401).json({
            success: false,
            message: "Unauthorized",
        });
    }

    try {
        await pool.query(
            `UPDATE active_sessions
             SET last_ping = NOW()
             WHERE employee_id = $1`,
            [employee_id]
        );

        return res.status(200).json({
            success: true,
        });

    } catch (error) {
        console.error("[HEARTBEAT ERROR]", error.message);
        return res.status(500).json({
            success: false,
            message: error.message,
        });
    }
};