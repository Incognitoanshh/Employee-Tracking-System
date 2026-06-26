const pool = require("../config/db");

// ── POST /logs/create ─────────────────────────────────────────
exports.createLog = async (req, res) => {
    // ✅ Token se employee_id
    const employee_id = req.user?.employee_id;
    const { activity } = req.body;

    if (!activity) {
        return res.status(400).json({
            success: false,
            message: "activity is required",
        });
    }

    try {
        await pool.query(
            `INSERT INTO activity_logs (employee_id, activity)
             VALUES ($1, $2)`,
            [employee_id, activity]
        );

        const { logAudit } = require("../utils/audit");
        await logAudit(employee_id, "Activity Sync");

        return res.status(200).json({ success: true });

    } catch (error) {
        console.error("[LOG CREATE ERROR]", error.message);
        return res.status(500).json({
            success: false,
            message: error.message,
        });
    }
};

// ── GET /logs/all ─────────────────────────────────────────────
exports.getLogs = async (req, res) => {
    const { employee_id: requesterId, role } = req.user;

    const page = Math.max(1, parseInt(req.query.page) || 1);
    const limit = 50;
    const offset = (page - 1) * limit;

    try {
        const conditions = [];
        const params = [];
        let idx = 1;

        // ✅ Role check
        if (role !== "admin") {
            conditions.push(`employee_id = $${idx++}`);
            params.push(requesterId);
        }

        const where = conditions.length
            ? `WHERE ${conditions.join(" AND ")}`
            : "";

        const countResult = await pool.query(
            `SELECT COUNT(*) FROM activity_logs ${where}`,
            params
        );

        const result = await pool.query(
            `SELECT id, employee_id, activity, created_at
             FROM activity_logs
             ${where}
             ORDER BY id DESC
             LIMIT $${idx} OFFSET $${idx + 1}`,
            [...params, limit, offset]
        );

        return res.status(200).json({
            success: true,
            data: result.rows,
            total: parseInt(countResult.rows[0].count),
            page,
        });

    } catch (error) {
        console.error("[LOG GET ERROR]", error.message);
        return res.status(500).json({
            success: false,
            message: error.message,
        });
    }
};

// ── POST /logs/upload ────────────────────────────────────────────
exports.uploadIdleLog = async (req, res) => {
    const employee_id = req.user?.employee_id;
    const {
        session_id,
        idle_start_ist,
        idle_end_ist,
        duration_seconds,
    } = req.body;

    if (!idle_start_ist || !idle_end_ist) {
        return res.status(400).json({
            success: false,
            message: "idle_start_ist and idle_end_ist are required",
        });
    }

    try {
        await pool.query(
            `INSERT INTO idle_logs (employee_id, session_id, idle_start, idle_end, duration_seconds)
             VALUES ($1, $2, $3, $4, $5)`,
            [employee_id, session_id, idle_start_ist, idle_end_ist, duration_seconds]
        );

        const { logAudit } = require("../utils/audit");
        await logAudit(employee_id, "Idle Detection");

        return res.status(200).json({ success: true });
    } catch (error) {
        console.error("[IDLE LOG UPLOAD ERROR]", error.message);
        return res.status(500).json({
            success: false,
            message: error.message,
        });
    }
};

// ── GET /logs/audit ────────────────────────────────────────────
// ── GET /admin/audit-logs ─────────────────────────────────────
exports.getAuditLogs = async (req, res) => {
    const { role } = req.user;

    if (role !== "admin") {
        return res.status(403).json({ success: false, message: "Access denied" });
    }

    const page = Math.max(1, parseInt(req.query.page) || 1);
    const limit = 50;
    const offset = (page - 1) * limit;
    const { employee_id, date } = req.query;

    try {
        const conditions = [];
        const params = [];
        let idx = 1;

        if (employee_id) {
            conditions.push(`employee_id = $${idx++}`);
            params.push(employee_id);
        }

        if (date) {
            conditions.push(`DATE(created_at) = $${idx++}`);
            params.push(date);   // "yyyy-MM-dd" format
        }

        const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";

        const countResult = await pool.query(
            `SELECT COUNT(*) FROM audit_logs ${where}`, params
        );

        const result = await pool.query(
            `SELECT id, employee_id, activity, created_at
             FROM audit_logs
             ${where}
             ORDER BY id DESC
             LIMIT $${idx} OFFSET $${idx + 1}`,
            [...params, limit, offset]
        );

        return res.status(200).json({
            success: true,
            data: result.rows,
            total: parseInt(countResult.rows[0].count),
            page,
        });

    } catch (error) {
        console.error("[AUDIT LOG GET ERROR]", error.message);
        return res.status(500).json({ success: false, message: error.message });
    }
};