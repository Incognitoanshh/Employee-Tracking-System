const pool = require("../config/db");

exports.getStats = async (req, res) => {

    try {

        const employees = await pool.query(
            "SELECT COUNT(*) FROM employees"
        );

        const screenshots = await pool.query(
            "SELECT COUNT(*) FROM screenshots"
        );

        const logs = await pool.query(
            "SELECT COUNT(*) FROM activity_logs"
        );

        return res.json({

            success: true,

            data: {

                employees:
                    Number(
                        employees.rows[0].count
                    ),

                screenshots:
                    Number(
                        screenshots.rows[0].count
                    ),

                activity_logs:
                    Number(
                        logs.rows[0].count
                    )

            }

        });

    } catch (error) {

        return res.status(500).json({

            success: false,

            error: error.message

        });

    }

};