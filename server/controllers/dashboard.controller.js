const pool = require("../config/db");
const { istDate, istToday, isTodayIST } = require("../utils/ist_sql");

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

        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Internal server error" });

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
        // FIX 4: sirf "recent" open sessions ko online maano. Pehle yahan
        // koi time-limit nahi thi — agar employee ka laptop crash ho jaye
        // ya app force-close ho (bina proper /attendance/logout call ke),
        // uska attendance row logout_time=NULL ke saath HAMESHA ke liye
        // "open" reh jaata — aur wo employee dashboard pe DINO tak "online"
        // dikhta rehta, chahe wo kabka offline ho chuka ho. Ab sirf wahi
        // open sessions online maane jaate hain jo pichhle 16 ghante ke
        // andar shuru hui hain (ek realistic max-shift-length se zyada
        // purani open session ka matlab hai wo genuinely abandoned/stale
        // hai, real "online" nahi).
        const online = await pool.query(`
            SELECT COUNT(DISTINCT a.employee_id) AS count
            FROM attendance a
            JOIN employees e ON e.employee_id = a.employee_id
            WHERE a.logout_time IS NULL
              AND a.login_time > (NOW() AT TIME ZONE 'UTC') - INTERVAL '16 hours'
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

        // The debug line that used to live here is gone. The dashboard polls
        // every few seconds while any admin has the panel open, so it wrote
        // the same figures into PM2's log file around seventeen thousand
        // times a day — burying the lines that mean something and filling a
        // disk that also holds the screenshots.

        return res.json({
            success: true,
            data: payload
        });

    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }

};

// Matches frontend recent activity feed list.
exports.getRecentActivity = async (req, res) => {
    const limit = Number(req.query.limit || 50);

    try {
        // BUG FIX: pehle broad '%ConfigSyncManager%'/'%SchedulerService%'
        // filter tha jo meaningful events (force_logout, shift updated,
        // scheduler start/stop) bhi Dashboard ke live feed se hide kar
        // deta tha. Ab sirf specific verbose-only noise patterns exclude
        // hote hain (consistent with getLogs/getEmployeeDetails).
        const logs = await pool.query(
            `SELECT created_at, activity
             FROM activity_logs
             WHERE activity NOT LIKE 'ConfigSyncManager: started%'
               AND activity NOT LIKE 'ConfigSyncManager: stopped%'
               AND activity NOT LIKE 'ConfigSyncManager: backoff%'
               AND activity NOT LIKE 'ConfigSyncManager: sync OK%'
               AND activity NOT LIKE 'ConfigSyncManager: server unreachable%'
               AND activity NOT LIKE 'ConfigSyncManager: request timed out%'
               AND activity NOT LIKE 'ConfigSyncManager: unexpected error%'
               AND activity NOT LIKE 'ConfigSyncManager: HTTP%'
               AND activity NOT LIKE 'SchedulerService: shift times not found%'
               AND activity NOT LIKE 'SchedulerService: shift already ended%'
               AND activity NOT LIKE 'SchedulerService: ConfigSync started%'
               AND activity NOT LIKE 'SchedulerService: screenshot scheduled%'
               AND activity NOT LIKE 'SchedulerService: config updated%'
               AND activity NOT LIKE 'SchedulerService: rescheduled%'
               AND activity NOT LIKE '%SYNC SAVE%'
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
        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
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
            AND activity NOT LIKE 'ConfigSyncManager: started%'
            AND activity NOT LIKE 'ConfigSyncManager: stopped%'
            AND activity NOT LIKE 'ConfigSyncManager: backoff%'
            AND activity NOT LIKE 'ConfigSyncManager: sync OK%'
            AND activity NOT LIKE 'ConfigSyncManager: server unreachable%'
            AND activity NOT LIKE 'ConfigSyncManager: request timed out%'
            AND activity NOT LIKE 'ConfigSyncManager: unexpected error%'
            AND activity NOT LIKE 'ConfigSyncManager: HTTP%'
            AND activity NOT LIKE 'SchedulerService: shift times not found%'
            AND activity NOT LIKE 'SchedulerService: shift already ended%'
            AND activity NOT LIKE 'SchedulerService: ConfigSync started%'
            AND activity NOT LIKE 'SchedulerService: screenshot scheduled%'
            AND activity NOT LIKE 'SchedulerService: config updated%'
            AND activity NOT LIKE 'SchedulerService: rescheduled%'
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
        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};

// ──────────────────────────────────────────────────────────────────────────────
//  GET /api/dashboard/me  — employee ka APNA aaj ka summary
//
//  Naya employee panel ("Today's Overview") ke liye. Ab tak saare dashboard
//  endpoints admin-scoped the (poori company ke counts) — employee ke apne
//  aaj ke numbers ke liye koi endpoint tha hi nahi, is liye panel ko sab kuch
//  local SQLite se guess karna padta (jo device badalne par galat ho jaata).
//
//  Sab kuch IST din ke hisaab se, aur SIRF requesting employee ka —
//  employee_id kabhi client se nahi liya jaata, hamesha JWT se.
// ──────────────────────────────────────────────────────────────────────────────
exports.getMySummary = async (req, res) => {
    const employeeId = req.employee?.employee_id;
    if (!employeeId) {
        return res.status(401).json({ success: false, message: "Unauthenticated" });
    }

    try {
        const IST_DAY = isTodayIST("created_at");

        const [shots, logs, session, totals] = await Promise.all([
            pool.query(
                `SELECT COUNT(*) FROM screenshots WHERE employee_id = $1 AND ${IST_DAY}`,
                [employeeId]
            ),
            pool.query(
                `SELECT COUNT(*) FROM activity_logs WHERE employee_id = $1 AND ${IST_DAY}`,
                [employeeId]
            ),
            pool.query(
                `SELECT login_time, logout_time
                 FROM attendance
                 WHERE employee_id = $1
                 ORDER BY id DESC LIMIT 1`,
                [employeeId]
            ),
            pool.query(
                `SELECT
                     COALESCE(SUM(
                         COALESCE(logout_time, (NOW() AT TIME ZONE 'UTC')) - login_time
                     ), interval '0') AS today_worked
                 FROM attendance
                 WHERE employee_id = $1
                   AND ${isTodayIST("login_time")}`,
                [employeeId]
            ),
        ]);

        // ── Aaj ka ACTIVE vs IDLE time ──
        //
        //  Employee panel ke "Today's Summary" me Active Time / Idle Time
        //  dikhta hai. Ye sirf ASLI attendance sessions ke andar count hota
        //  hai — app band rehne ka time kabhi nahi judta (wahi bug jo
        //  getEmployeeDetails me 801 ghante dikha raha tha).
        const todaySessions = await pool.query(
            `SELECT login_time,
                    COALESCE(logout_time, (NOW() AT TIME ZONE 'UTC')) AS end_time
             FROM attendance
             WHERE employee_id = $1
               AND ${isTodayIST("login_time")}
             ORDER BY login_time ASC`,
            [employeeId]
        );
        const todayEvents = await pool.query(
            `SELECT created_at, activity
             FROM activity_logs
             WHERE employee_id = $1
               AND (UPPER(activity) LIKE '%USER ACTIVE%' OR UPPER(activity) LIKE '%USER IDLE%')
               AND ${isTodayIST("created_at")}
             ORDER BY created_at ASC
             LIMIT 20000`,
            [employeeId]
        );

        const utc = (s) => new Date(String(s).replace(" ", "T") + "Z").getTime();
        const evts = todayEvents.rows
            .map(r => ({
                t: utc(r.created_at),
                s: String(r.activity).toUpperCase().includes("USER IDLE") ? "IDLE" : "ACTIVE",
            }))
            .filter(e => Number.isFinite(e.t));

        let activeMs = 0, idleMs = 0;
        for (const sess of todaySessions.rows) {
            const start = utc(sess.login_time);
            const end   = utc(sess.end_time);
            if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) continue;
            let cursor = start, state = "ACTIVE";
            for (const e of evts.filter(x => x.t >= start && x.t <= end)) {
                const dt = e.t - cursor;
                if (dt > 0) { if (state === "ACTIVE") activeMs += dt; else idleMs += dt; }
                state = e.s; cursor = e.t;
            }
            const tail = end - cursor;
            if (tail > 0) { if (state === "ACTIVE") activeMs += tail; else idleMs += tail; }
        }

        // Upload health — kitna data abhi tak sync ho chuka hai.
        const pending = await pool.query(
            `SELECT COUNT(*) FROM screenshots WHERE employee_id = $1 AND ${IST_DAY}`,
            [employeeId]
        );

        const row = session.rows[0] || {};
        return res.json({
            success: true,
            data: {
                screenshots_today: Number(shots.rows[0].count),
                logs_today:        Number(logs.rows[0].count),
                session_start:     row.login_time || null,
                session_open:      Boolean(row.login_time && !row.logout_time),
                today_worked:      totals.rows[0].today_worked,
                active_seconds:    Math.round(activeMs / 1000),
                idle_seconds:      Math.round(idleMs / 1000),
                synced_today:      Number(pending.rows[0].count),
            },
        });
    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};
