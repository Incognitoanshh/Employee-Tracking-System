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
// ── THE MINUTES A CLIENT SCORED ────────────────────────────────────────
//
// One row a minute, in batches: a laptop that was off the network for an
// hour has sixty waiting, and sixty round trips on a hotel connection is how
// a sync falls behind and stays behind.
//
// ALWAYS FOR THE CALLER. The employee id comes from the token and nothing
// else; there is no field here that could be pointed at somebody else.
const ACTIVITY_BATCH_LIMIT = 500;

exports.recordActivityMinutes = async (req, res) => {
    const employeeId = req.employee?.employee_id;
    if (!employeeId) {
        return res.status(401).json({ success: false, message: "Not authenticated" });
    }
    const minutes = Array.isArray(req.body?.minutes) ? req.body.minutes : null;
    if (!minutes || minutes.length === 0) {
        return res.status(400).json({ success: false, message: "minutes must be a non-empty array" });
    }
    if (minutes.length > ACTIVITY_BATCH_LIMIT) {
        return res.status(400).json({
            success: false,
            message: `At most ${ACTIVITY_BATCH_LIMIT} minutes in one batch`,
        });
    }

    const rows = [];
    for (const entry of minutes) {
        const minute = String(entry?.minute || "");
        if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$/.test(minute)) {
            return res.status(400).json({
                success: false,
                message: "each minute must be YYYY-MM-DD HH:MM",
            });
        }
        const score = Number(entry?.score);
        if (!Number.isFinite(score) || score < 0 || score > 100) {
            return res.status(400).json({ success: false, message: "score must be 0-100" });
        }
        const whole = (value) => {
            const number = Number(value);
            return Number.isFinite(number) && number >= 0 ? Math.floor(number) : 0;
        };
        rows.push([
            employeeId, minute, Math.round(score),
            ["ACTIVE", "LOW", "IDLE"].includes(String(entry?.band))
                ? String(entry.band) : "IDLE",
            whole(entry?.keystrokes), whole(entry?.clicks), whole(entry?.scrolls),
            whole(entry?.mouse_moves), whole(entry?.window_changes),
            entry?.automation_suspected === true || entry?.automation_suspected === 1,
            String(entry?.reasons || "").slice(0, 300),
        ]);
    }

    try {
        // ONE STATEMENT, NOT ONE PER ROW. And the same minute sent twice —
        // a batch that was accepted and then retried after a dropped
        // connection — updates rather than doubling the day.
        const values = rows.map((_row, i) => {
            const at = i * 11;
            return `($${at + 1}, $${at + 2}::timestamp, $${at + 3}, $${at + 4}, $${at + 5}, `
                 + `$${at + 6}, $${at + 7}, $${at + 8}, $${at + 9}, $${at + 10}, $${at + 11})`;
        }).join(", ");
        await pool.query(
            `INSERT INTO activity_minutes
                 (employee_id, minute, score, band, keystrokes, clicks, scrolls,
                  mouse_moves, window_changes, automation_suspected, reasons)
             VALUES ${values}
             ON CONFLICT (employee_id, minute) DO UPDATE
                 SET score = EXCLUDED.score, band = EXCLUDED.band,
                     keystrokes = EXCLUDED.keystrokes, clicks = EXCLUDED.clicks,
                     scrolls = EXCLUDED.scrolls, mouse_moves = EXCLUDED.mouse_moves,
                     window_changes = EXCLUDED.window_changes,
                     automation_suspected = EXCLUDED.automation_suspected,
                     reasons = EXCLUDED.reasons`,
            rows.flat());
        return res.json({ success: true, stored: rows.length });
    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

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