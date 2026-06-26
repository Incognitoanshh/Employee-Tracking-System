const pool = require("../config/db");
const path = require("path");
const fs   = require("fs");

exports.uploadScreenshot = async (req, res) => {
    // ✅ req.user — verifyToken middleware se
    const { employee_id } = req.user;

    if (!req.file) {
        return res.status(400).json({
            success: false,
            message: "No file uploaded",
        });
    }

    try {
        // ✅ session_id aur timestamp bhi save karo
        const session_id  = req.body.session_id  || null;
        const timestamp   = req.body.timestamp
            || new Date().toISOString();

        const result = await pool.query(
            `INSERT INTO screenshots
                (employee_id, session_id, file_name, timestamp)
             VALUES ($1, $2, $3, $4)
             RETURNING id`,
            [employee_id, session_id, req.file.filename, timestamp]
        );

        console.log(
            "[UPLOAD] employee:", employee_id,
            "file:", req.file.filename,
            "id:", result.rows[0].id
        );

        const { logAudit } = require("../utils/audit");
        await logAudit(employee_id, "Screenshot Captured");
        await logAudit(employee_id, "Screenshot Uploaded");

        // ✅ Spec format
        return res.status(200).json({
            status:        "success",
            screenshot_id: result.rows[0].id,
        });

    } catch (error) {
        console.error("[UPLOAD ERROR]", error.message);
        return res.status(500).json({
            success: false,
            message: error.message,
        });
    }
};

exports.getScreenshots = async (req, res) => {
    const { employee_id, role } = req.user;

    // ✅ Pagination
    const page  = Math.max(1, parseInt(req.query.page)  || 1);
    const limit = 20;
    const offset = (page - 1) * limit;

    // ✅ Filters
    const filter_emp  = req.query.employee_id || null;
    const filter_date = req.query.date        || null;

    try {
        let conditions = [];
        let params     = [];
        let idx        = 1;

        // Role check — employee sirf apna dekh sakta hai
        if (role !== "admin") {
            conditions.push(`employee_id = $${idx++}`);
            params.push(employee_id);
        } else if (filter_emp) {
            conditions.push(`employee_id = $${idx++}`);
            params.push(filter_emp);
        }

        if (filter_date) {
            conditions.push(`DATE(timestamp) = $${idx++}`);
            params.push(filter_date);
        }

        const where = conditions.length
            ? `WHERE ${conditions.join(" AND ")}`
            : "";

        // Total count
        const countResult = await pool.query(
            `SELECT COUNT(*) FROM screenshots ${where}`,
            params
        );
        const total = parseInt(countResult.rows[0].count);

        // ✅ Paginated results
        const result = await pool.query(
            `SELECT id, employee_id, file_name, timestamp, session_id
             FROM screenshots
             ${where}
             ORDER BY timestamp DESC
             LIMIT $${idx} OFFSET $${idx + 1}`,
            [...params, limit, offset]
        );

        return res.status(200).json({
            success: true,
            data:    result.rows,
            total,
            page,
            limit,
        });

    } catch (error) {
        console.error("[GET SCREENSHOTS ERROR]", error.message);
        return res.status(500).json({
            success: false,
            message: error.message,
        });
    }
};

exports.downloadScreenshot = async (req, res) => {
    const { id } = req.params;
    const { employee_id: requesterId, role } = req.user;

    try {
        const result = await pool.query(
            `SELECT employee_id, file_name FROM screenshots WHERE id = $1`,
            [id]
        );

        if (result.rows.length === 0) {
            return res.status(404).json({
                success: false,
                message: "Screenshot not found",
            });
        }

        const { employee_id: ownerId, file_name } = result.rows[0];

        if (ownerId !== requesterId && role !== "admin") {
            return res.status(403).json({
                success: false,
                message: "Access denied",
            });
        }

        // ✅ Path traversal prevent
        const safe     = path.basename(file_name);
        const fullPath = path.resolve(
            __dirname, "../uploads/screenshots", safe
        );

        if (!fs.existsSync(fullPath)) {
            return res.status(404).json({
                success: false,
                message: "File not found on server",
            });
        }

        res.download(fullPath);

    } catch (error) {
        console.error("[DOWNLOAD ERROR]", error.message);
        return res.status(500).json({
            success: false,
            message: error.message,
        });
    }
};