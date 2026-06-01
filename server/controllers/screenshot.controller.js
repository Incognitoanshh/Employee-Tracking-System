const pool = require("../config/db");

exports.uploadScreenshot = async (req, res) => {

    console.log("SCREENSHOT API HIT");

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
                "EMP001",
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

    try {

        const result = await pool.query(
            `
            SELECT *
            FROM screenshots
            ORDER BY id DESC
            LIMIT 100
            `
        );

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