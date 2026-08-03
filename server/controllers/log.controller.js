const pool = require("../config/db");

// super_admin ko har jagah admin ke barabar (ya usse upar) treat karo.
// BUG: ye checks pehle sirf "admin" dekhte the, is liye super_admin ko
// sirf apna hi data dikhta — poori company ka nahi.
const isElevated = (role) => role === "admin" || role === "super_admin";


exports.createLog = async (req, res) => {

    try {

        let {

            employee_id,
            activity

        } = req.body;

        // SECURITY FIX: non-admin employees sirf apne naam se log create
        // kar sakte hain. Pehle koi bhi employee body mein kisi aur ka
        // employee_id bhej ke uske naam se fake activity log daal sakta tha.
        if (!isElevated(req.employee?.role)) {
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

        // BUG FIX: response me sirf `data` (max 100 rows) jaata tha, koi
        // total nahi. Client dashboard ka "Logs Recorded" card `len(data)`
        // gin ke dikhata tha — matlab 100 logs ke baad wo card HAMESHA
        // "100" pe atka rehta tha, chahe employee ke 5000 logs ho jayen.
        // Employee ko lagta tha counter kaam hi nahi kar raha (bilkul sahi
        // observation). Ab alag se asli COUNT bhejte hain.
        let result;
        let totalResult;
        if (isElevated(role)) {
            result = await pool.query(
                `SELECT * FROM activity_logs ORDER BY id DESC LIMIT 100`
            );
            totalResult = await pool.query(
                `SELECT COUNT(*) FROM activity_logs`
            );
        } else {
            result = await pool.query(
                `SELECT * FROM activity_logs WHERE employee_id = $1 ORDER BY id DESC LIMIT 100`,
                [requestingEmployee]
            );
            totalResult = await pool.query(
                `SELECT COUNT(*) FROM activity_logs WHERE employee_id = $1`,
                [requestingEmployee]
            );
        }

        return res.json({
            success: true,
            data:  result.rows,
            total: Number(totalResult.rows[0].count),
        });

    }

    catch (error) {
        return res.status(500).json({
            success: false,
            error: error.message
        });

    }

};