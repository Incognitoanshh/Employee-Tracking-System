const pool = require("../config/db");

exports.getAttendance = async (req, res) => {
    try {
        let { employee_id, page = 1 } = req.query;
        const limit  = 50;
        const offset = (page - 1) * limit;

        // SECURITY: non-admin apna data hi dekh sakte hain
        if (req.employee?.role !== "admin") {
            employee_id = req.employee?.employee_id;
        }

        const conditions = [];
        const values     = [];
        let   idx        = 1;

        if (employee_id) {
            conditions.push(`employee_id = $${idx++}`);
            values.push(employee_id);
        }

        const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";

        const result = await pool.query(
            `SELECT id, employee_id, login_time, logout_time, total_hours
             FROM attendance ${where}
             ORDER BY id DESC
             LIMIT $${idx} OFFSET $${idx + 1}`,
            [...values, limit, offset]
        );

        const countResult = await pool.query(
            `SELECT COUNT(*) FROM attendance ${where}`, values
        );

        return res.json({
            success: true,
            data:    result.rows,
            total:   Number(countResult.rows[0].count),
            page:    Number(page),
        });

    } catch (error) {
        return res.status(500).json({ success: false, error: error.message });
    }
};

exports.loginAttendance = async (req, res) => {
    try {
        let { employee_id, login_time } = req.body;

        if (req.employee?.role !== "admin") {
            employee_id = req.employee?.employee_id;
        }

        if (!employee_id) {
            return res.status(400).json({ success: false, error: "employee_id required" });
        }

        // Close any open sessions (crash/force-close se pehle wali session
        // jo kabhi properly logout nahi hui — naye login se pehle safety-net
        // ke taur pe band kar do). total_hours yaha bhi NOW() - login_time
        // se compute karo (NULL chhodne ki jagah) — self-consistent rehta
        // hai (isi row ke apne dono timestamps se), display pe "—" ki jagah
        // ek meaningful (best-effort) duration dikhega.
        await pool.query(
            `UPDATE attendance SET logout_time = NOW(), total_hours = NOW()::timestamp - login_time
             WHERE employee_id = $1 AND logout_time IS NULL`,
            [employee_id]
        );

        // BUG FIX: login_time ko UTC mein store karo
        // Client IST string bhejta tha jaise "2026-06-28 15:30:00"
        // PostgreSQL bina timezone ke store karta tha — inconsistent tha
        // Ab hum seedha NOW() use karte hain jo UTC mein hai
        const result = await pool.query(
            `INSERT INTO attendance (employee_id, login_time) VALUES ($1, NOW()) RETURNING id`,
            [employee_id]
        );

        res.json({ success: true, id: result.rows[0].id });

    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
};

exports.logoutAttendance = async (req, res) => {
    try {
        let { employee_id } = req.body;

        if (req.employee?.role !== "admin") {
            employee_id = req.employee?.employee_id;
        }

        if (!employee_id) {
            return res.status(400).json({ success: false, error: "employee_id required" });
        }

        // total_hours client se LIYA NAHI jata — client ka local session
        // (SQLite `shifts` row) server ke actual attendance row se DISCONNECT
        // ho sakta hai (e.g. auto-login ke baad purani open server-session
        // continue hoti hai lekin naya chhota local shift row bhi ban jaata
        // hai) — is wajah se client ka duration calculation kabhi bahut
        // chhota (jaise "8 minutes") ho sakta hai jabki actual server session
        // ghanton lambi thi. Server khud NOW() - login_time se authoritative
        // total_hours compute karta hai — dono values USI row se aate hain,
        // isliye kabhi mismatch nahi ho sakta.
        const result = await pool.query(
            `UPDATE attendance
             SET logout_time = NOW(), total_hours = NOW()::timestamp - login_time
             WHERE id = (
                 SELECT id FROM attendance
                 WHERE employee_id = $1 AND logout_time IS NULL
                 ORDER BY id DESC LIMIT 1
             )
             RETURNING id`,
            [employee_id]
        );

        if (result.rows.length === 0) {
            return res.status(404).json({ success: false, error: "No open attendance session found" });
        }

        res.json({ success: true });

    } catch (error) {
        res.status(500).json({ success: false, error: error.message });
    }
};
