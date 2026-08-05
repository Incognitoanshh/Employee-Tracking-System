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

        } = req.body || {};

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

        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Internal server error" });

    }

};

/**
 * The client reporting how much of a day it spent idle.
 *
 * Upsert rather than insert: a day's total keeps growing while the employee
 * is signed in, so the client re-sends the same day whenever it changes.
 *
 * GREATEST() so a stale client cannot walk the number backwards. Two devices
 * signed in as the same person each hold their own running total, and
 * whichever posts second would otherwise overwrite the larger figure with
 * its own smaller one.
 */
exports.recordIdleDaily = async (req, res) => {
    const { day, idle_seconds } = req.body || {};
    const employeeId = req.employee?.employee_id;

    if (!employeeId) {
        return res.status(401).json({ success: false, message: "Not authenticated" });
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(day || ""))) {
        return res.status(400).json({ success: false, message: "day must be YYYY-MM-DD" });
    }
    const seconds = parseInt(idle_seconds, 10);
    // A day holds 86400 seconds; anything past that is a broken client, not
    // a very tired employee.
    if (!Number.isFinite(seconds) || seconds < 0 || seconds > 86400) {
        return res.status(400).json({ success: false, message: "idle_seconds must be 0-86400" });
    }

    try {
        await pool.query(
            `INSERT INTO idle_daily (employee_id, day, idle_seconds, updated_at)
             VALUES ($1, $2, $3, NOW())
             ON CONFLICT (employee_id, day) DO UPDATE
                 SET idle_seconds = GREATEST(idle_daily.idle_seconds, EXCLUDED.idle_seconds),
                     updated_at = NOW()`,
            [employeeId, day, seconds]
        );
        return res.json({ success: true });
    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
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
        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Internal server error" });

    }

};