const pool = require("../config/db");

exports.createLog = async (req, res) => {

    try {

        let {

            employee_id,
            activity

        } = req.body;

        // SECURITY FIX: non-admin employees sirf apne naam se log create
        // kar sakte hain. Pehle koi bhi employee body mein kisi aur ka
        // employee_id bhej ke uske naam se fake activity log daal sakta tha.
        if (req.employee?.role !== "admin") {
            employee_id = req.employee?.employee_id;
        }

        if (!employee_id || !activity) {
            return res.status(400).json({
                success: false,
                error: "employee_id and activity are required"
            });
        }

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

        // BUG FIX: Pehle yeh query SAARE employees ke logs return karti thi,
        // koi employee/role filter nahi tha. Koi bhi logged-in employee
        // /api/logs/all call karke har employee ke activity logs dekh sakta tha.
        // Ab: admin sabka dekh sakta hai, employee sirf apna.
        const role = req.employee?.role;
        const requestingEmployee = req.employee?.employee_id;

        let result;
        if (role === "admin") {
            result = await pool.query(
                `SELECT * FROM activity_logs ORDER BY id DESC LIMIT 100`
            );
        } else {
            result = await pool.query(
                `SELECT * FROM activity_logs WHERE employee_id = $1 ORDER BY id DESC LIMIT 100`,
                [requestingEmployee]
            );
        }

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