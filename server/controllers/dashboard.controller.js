const pool = require("../config/db");

exports.getStats = async (req, res) => {

    try {

        const employees = await pool.query(
            "SELECT COUNT(*) FROM employees WHERE role = 'employee'"
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

// Matches frontend admin dashboard cards keys
exports.getAdminSummary = async (req, res) => {

    try {
        // FIX 1: Sirf role='employee' count karo, admin nahi
        const employees = await pool.query(
            "SELECT COUNT(*) FROM employees WHERE role = 'employee'"
        );

        const totalEmployees = Number(employees.rows[0].count || 0);

        // FIX 2: attendance JOIN employees — admin ki open session exclude karo
        const online = await pool.query(`
            SELECT COUNT(DISTINCT a.employee_id) AS count
            FROM attendance a
            JOIN employees e ON e.employee_id = a.employee_id
            WHERE a.logout_time IS NULL
              AND e.role = 'employee'
        `);

        const onlineCount = Number(online.rows[0].count || 0);

        // FIX 3: offline = total employees - online employees (admin already excluded above)
        const offlineCount = Math.max(0, totalEmployees - onlineCount);

        const screenshots = await pool.query(
            "SELECT COUNT(*) FROM screenshots"
        );

        const logs = await pool.query(
            "SELECT COUNT(*) FROM activity_logs"
        );

        const payload = {
            total_employees: totalEmployees,
            online_employees: onlineCount,
            offline_employees: offlineCount,
            total_screenshots: Number(screenshots.rows[0].count || 0),
            total_activity_logs: Number(logs.rows[0].count || 0),
        };

        console.log(
            "[ADMIN SUMMARY RAW]",
            {
                totalEmployees,
                onlineCount,
                offlineCount,
                screenshots_count: Number(screenshots.rows[0].count || 0),
                logs_count: Number(logs.rows[0].count || 0),
                payload
            }
        );

        return res.json({
            success: true,
            data: payload
        });

    } catch (error) {
        return res.status(500).json({
            success: false,
            error: error.message
        });
    }

};

// Matches frontend recent activity feed list.
exports.getRecentActivity = async (req, res) => {
    const limit = Number(req.query.limit || 50);

    try {
        const logs = await pool.query(
            `SELECT created_at, activity
             FROM activity_logs
             WHERE activity NOT LIKE '%ConfigSyncManager%'
               AND activity NOT LIKE '%SchedulerService%'
               AND activity NOT LIKE '%SYNC SAVE%'
               AND activity NOT LIKE '%sync OK%'
             ORDER BY id DESC
             LIMIT $1`,
            [limit]
        );

        // Frontend expects array items with { message: str, created_at?: str }
        const items = logs.rows.map(r => {
            return {
                message: r.activity,
                created_at: r.created_at
            };
        });

        return res.json({
            success: true,
            data: {
                recent_activity: items
            }
        });

    } catch (error) {
        return res.status(500).json({
            success: false,
            error: error.message
        });
    }

};

// Charts data - last 7 days
exports.getChartsData = async (req, res) => {
    try {
        const screenshots = await pool.query(`
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM screenshots
            WHERE created_at >= NOW() - INTERVAL '7 days'
            GROUP BY DATE(created_at)
            ORDER BY date ASC
        `);

        const attendance = await pool.query(`
            SELECT DATE(login_time) as date, COUNT(DISTINCT employee_id) as count
            FROM attendance
            WHERE login_time >= NOW() - INTERVAL '7 days'
            AND employee_id IN (SELECT employee_id FROM employees WHERE role = 'employee')
            GROUP BY DATE(login_time)
            ORDER BY date ASC
        `);

        const activity = await pool.query(`
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM activity_logs
            WHERE created_at >= NOW() - INTERVAL '7 days'
            AND activity NOT LIKE '%ConfigSyncManager%'
            AND activity NOT LIKE '%SchedulerService%'
            GROUP BY DATE(created_at)
            ORDER BY date ASC
        `);

        return res.json({
            success: true,
            data: {
                screenshots_per_day: screenshots.rows,
                attendance_per_day: attendance.rows,
                activity_per_day: activity.rows
            }
        });
    } catch (error) {
        return res.status(500).json({ success: false, error: error.message });
    }
};
