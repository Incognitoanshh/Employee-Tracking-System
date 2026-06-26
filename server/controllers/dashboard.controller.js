const pool = require("../config/db");

// ── GET /dashboard/stats ──────────────────────────────────────
// Employee dashboard ke liye basic stats
exports.getStats = async (req, res) => {
    const { employee_id } = req.user;

    try {
        const screenshots = await pool.query(
            `SELECT COUNT(*) FROM screenshots WHERE employee_id = $1`,
            [employee_id]
        );

        const logs = await pool.query(
            `SELECT COUNT(*) FROM activity_logs WHERE employee_id = $1`,
            [employee_id]
        );

        return res.status(200).json({
            success: true,
            data: {
                activity_logs: Number(logs.rows[0].count || 0),
                screenshots:   Number(screenshots.rows[0].count || 0),
            },
        });

    } catch (error) {
        console.error("[STATS ERROR]", error.message);
        return res.status(500).json({
            success: false,
            message: error.message,
        });
    }
};

// ── GET /dashboard/summary ────────────────────────────────────
// Admin panel _DashboardTab ke liye
exports.getAdminSummary = async (req, res) => {
    try {
        // Sirf employees — admin exclude
        const employees = await pool.query(
            `SELECT COUNT(*) FROM employees WHERE role = 'employee'`
        );
        const totalEmployees = Number(employees.rows[0].count || 0);

        // Online = active_sessions (matching Employees page logic)
        const online = await pool.query(`
            SELECT COUNT(*) AS count
            FROM active_sessions s
            JOIN employees e ON e.employee_id = s.employee_id
            WHERE e.role = 'employee'
        `);
        const onlineCount  = Number(online.rows[0].count || 0);
        const offlineCount = Math.max(0, totalEmployees - onlineCount);

        const screenshots = await pool.query(
            `SELECT COUNT(*) FROM screenshots`
        );

        const logs = await pool.query(
            `SELECT COUNT(*) FROM activity_logs`
        );

        return res.status(200).json({
            success: true,
            data: {
                total_employees:     totalEmployees,
                online_employees:    onlineCount,
                offline_employees:   offlineCount,
                total_screenshots:   Number(screenshots.rows[0].count || 0),
                total_activity_logs: Number(logs.rows[0].count || 0),
            },
        });

    } catch (error) {
        console.error("[ADMIN SUMMARY ERROR]", error.message);
        return res.status(500).json({
            success: false,
            message: error.message,
        });
    }
};

// ── GET /dashboard/recent-activity ───────────────────────────
// Admin panel activity feed
exports.getRecentActivity = async (req, res) => {
    const limit = Math.min(Number(req.query.limit || 50), 100);

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

        return res.status(200).json({
            success: true,
            data: {
                recent_activity: logs.rows.map(r => ({
                    message:    r.activity,
                    created_at: r.created_at,
                })),
            },
        });

    } catch (error) {
        console.error("[RECENT ACTIVITY ERROR]", error.message);
        return res.status(500).json({
            success: false,
            message: error.message,
        });
    }
};

// ── GET /dashboard/charts ─────────────────────────────────────
// Admin panel bar charts — last 7 days
exports.getChartsData = async (req, res) => {
    try {
        // ✅ screenshots table mein column = timestamp (created_at nahi)
        const screenshots = await pool.query(`
            SELECT DATE(timestamp) AS date, COUNT(*) AS count
            FROM screenshots
            WHERE timestamp >= NOW() - INTERVAL '7 days'
            GROUP BY DATE(timestamp)
            ORDER BY date ASC
        `);

        const attendance = await pool.query(`
            SELECT DATE(login_time) AS date,
                   COUNT(DISTINCT a.employee_id) AS count
            FROM attendance a
            JOIN employees e ON e.employee_id = a.employee_id
            WHERE a.login_time >= NOW() - INTERVAL '7 days'
              AND e.role = 'employee'
            GROUP BY DATE(login_time)
            ORDER BY date ASC
        `);

        const activity = await pool.query(`
            SELECT DATE(created_at) AS date, COUNT(*) AS count
            FROM activity_logs
            WHERE created_at >= NOW() - INTERVAL '7 days'
              AND activity NOT LIKE '%ConfigSyncManager%'
              AND activity NOT LIKE '%SchedulerService%'
            GROUP BY DATE(created_at)
            ORDER BY date ASC
        `);

        return res.status(200).json({
            success: true,
            data: {
                screenshots_per_day: screenshots.rows,
                attendance_per_day:  attendance.rows,
                activity_per_day:    activity.rows,
            },
        });

    } catch (error) {
        console.error("[CHARTS ERROR]", error.message);
        return res.status(500).json({
            success: false,
            message: error.message,
        });
    }
};