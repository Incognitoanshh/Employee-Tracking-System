const pool = require("../config/db");
const path = require("path");
const fs = require("fs");

exports.uploadScreenshot = async (req, res) => {

    console.log("EMPLOYEE ID:", req.employee);

    const employee_id =
    req.employee.employee_id;

    console.log("SCREENSHOT API HIT");

    if (!req.file) {
        return res.status(400).json({
            success: false,
            message: "No file uploaded"
        });
    }

    try {
        console.log("UPLOAD FROM EMPLOYEE:", employee_id);

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

        const fullPath = path.resolve(__dirname, "../uploads/screenshots", file_name);

        console.log("[DOWNLOAD] id:", id, "file_name:", file_name, "fullPath:", fullPath);

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