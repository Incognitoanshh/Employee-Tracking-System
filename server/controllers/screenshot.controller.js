const pool = require("../config/db");
const path = require("path");
const fs = require("fs");

exports.uploadScreenshot = async (req, res) => {

    const employee_id =
    req.employee.employee_id;

    if (!req.file) {
        return res.status(400).json({
            success: false,
            message: "No file uploaded"
        });
    }

    try {

        await pool.query(
            `
            INSERT INTO screenshots
            (
                employee_id,
                file_name
            )
            VALUES
            (
                $1,
                $2
            )
            `,
            [
                employee_id,
                req.file.filename
            ]
        );

        return res.json({
            success: true,
            file: req.file.filename
        });

    } catch (error) {

        return res.status(500).json({
            success: false,
            error: error.message
        });

    }

};

exports.getScreenshots = async (req, res) => {
    const requestingEmployee = req.employee.employee_id;
    const role = req.employee.role;

    try {

        let query = '';
        let params = [];

        if (role === 'admin') {
            // Admin sees all screenshots
            query = `SELECT * FROM screenshots ORDER BY id DESC LIMIT 100`;
        } else {
            // Employee sees only own screenshots
            query = `SELECT * FROM screenshots WHERE employee_id = $1 ORDER BY id DESC LIMIT 100`;
            params = [requestingEmployee];
        }

        const result = await pool.query(query, params);

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

exports.downloadScreenshot = async (req, res) => {
    try {
        const { id } = req.params;
        const requestingEmployee = req.employee.employee_id;
        const role = req.employee.role;

        const result = await pool.query(
            `SELECT employee_id, file_name, created_at FROM screenshots WHERE id = $1`,
            [id]
        );

        if (result.rows.length === 0) {
            return res.status(404).json({ success: false, error: "Screenshot not found" });
        }

        const { employee_id: ownerId, file_name, created_at } = result.rows[0];

        // ACCESS CHECK: Only owner or admin can download
        if (ownerId !== requestingEmployee && role !== 'admin') {
            return res.status(403).json({
                success: false,
                error: "Access denied. You can only download your own screenshots."
            });
        }

        const uploadDir = process.env.UPLOAD_DIR
            ? path.resolve(process.env.UPLOAD_DIR)
            : path.resolve(__dirname, "../uploads/screenshots");

        // SECURITY HARDENING: file_name is expected to already be sanitized
        // (it comes from multer's filename callback at upload time), but we
        // never trust a DB value for filesystem access without re-validating.
        // path.basename() strips any directory components, and we additionally
        // confirm the resolved path is still inside uploadDir before touching
        // the filesystem — defense-in-depth against path traversal if file_name
        // is ever populated some other way (manual DB edit, future code path, etc).
        const safeFileName = path.basename(file_name);
        const fullPath = path.resolve(uploadDir, safeFileName);

        if (!fullPath.startsWith(uploadDir + path.sep) && fullPath !== uploadDir) {
            console.log("[DOWNLOAD] Rejected path traversal attempt:", file_name);
            return res.status(400).json({ success: false, error: "Invalid file reference" });
        }

        console.log("[DOWNLOAD] id:", id, "file_name:", safeFileName, "fullPath:", fullPath);

        if (!fs.existsSync(fullPath)) {
            console.log("[DOWNLOAD] File not found:", fullPath);
            return res.status(404).json({ success: false, error: "File not found on server" });
        }

        res.download(fullPath);

    } catch (error) {
        console.log("[DOWNLOAD] Error:", error.message);
        return res.status(500).json({ success: false, error: error.message });
    }
};