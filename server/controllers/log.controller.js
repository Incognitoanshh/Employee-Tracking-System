const pool = require("../config/db");

exports.createLog = async (req, res) => {

    try {

        const {

            employee_id,
            activity

        } = req.body;

        await pool.query(

            `
            INSERT INTO activity_logs
            (
                employee_id,
                activity
            )
            VALUES
            (
                $1,
                $2
            )
            `,

            [
                employee_id,
                activity
            ]

        );

        return res.json({

            success: true

        });

    }

    catch (error) {

        return res.status(500).json({

            success: false,

            error: error.message

        });

    }

};

exports.getLogs = async (req, res) => {

    try {

        const result = await pool.query(
            `
            SELECT *
            FROM activity_logs
            ORDER BY id DESC
            LIMIT 100
            `
        );

        return res.json({
            success: true,
            data: result.rows
        });

    }

    catch (error) {

        return res.status(500).json({
            success: false,
            error: error.message
        });

    }

};