const pool = require("../config/db");

// ── GET /attendance/all ───────────────────────────────────────
exports.getAttendance = async (req, res) => {
    const { employee_id: requesterId, role } = req.user;

    // ✅ Filters
    const filter_emp  = req.query.employee_id || null;
    const filter_date = req.query.date        || null;
    const page        = Math.max(1, parseInt(req.query.page) || 1);
    const limit       = 50;
    const offset      = (page - 1) * limit;

    try {
        const conditions = [];
        const params     = [];
        let   idx        = 1;

        // ✅ Role check
        if (role !== "admin") {
            conditions.push(`employee_id = $${idx++}`);
            params.push(requesterId);
        } else if (filter_emp) {
            conditions.push(`employee_id = $${idx++}`);
            params.push(filter_emp);
        }

        if (filter_date) {
            conditions.push(`DATE(login_time) = $${idx++}`);
            params.push(filter_date);
        }

        const where = conditions.length
            ? `WHERE ${conditions.join(" AND ")}`
            : "";

        const countResult = await pool.query(
            `SELECT COUNT(*) FROM attendance ${where}`,
            params
        );
        const total = parseInt(countResult.rows[0].count);

        const result = await pool.query(
            `SELECT id, employee_id, login_time, logout_time, total_hours
             FROM attendance
             ${where}
             ORDER BY id DESC
             LIMIT $${idx} OFFSET $${idx + 1}`,
            [...params, limit, offset]
        );

        return res.status(200).json({
            success: true,
            data:    result.rows,
            total,
            page,
        });

    } catch (error) {
        console.error("[ATTENDANCE GET ERROR]", error.message);
        return res.status(500).json({
            success: false,
            message: error.message,
        });
    }
};

// ── POST /attendance/login ────────────────────────────────────
exports.loginAttendance = async (req, res) => {
    // ✅ Token se employee_id — body pe trust nahi
    const employee_id = req.user.employee_id;

    // Parse ISO8601 timestamp from client — handle both with and without timezone
    let login_time;
    const rawLoginTime = req.body.login_time;
    if (rawLoginTime) {
        // Client sends: YYYY-MM-DD HH:MM:SS or ISO8601
        // Parse as date and convert to UTC for PostgreSQL
        const parsed = new Date(rawLoginTime);
        if (isNaN(parsed.getTime())) {
            console.error("[ATTENDANCE LOGIN] Invalid login_time:", rawLoginTime);
            return res.status(400).json({
                success: false,
                message: "Invalid login_time format",
            });
        }
        login_time = parsed.toISOString();
    } else {
        login_time = new Date().toISOString();
    }

    console.log("[ATTENDANCE LOGIN]", employee_id, "login_time:", login_time);

    try {
        // Close old open records — calculate total_hours as INTERVAL
        const oldRecord = await pool.query(
            `SELECT id, login_time FROM attendance
             WHERE employee_id = $1
               AND logout_time IS NULL
             ORDER BY id DESC LIMIT 1`,
            [employee_id]
        );

        if (oldRecord.rows.length > 0) {
            const oldId = oldRecord.rows[0].id;
            const oldLoginTime = oldRecord.rows[0].login_time;

            // Validate: new login_time >= old login_time
            const newLoginDt = new Date(login_time);
            const oldLoginDt = new Date(oldLoginTime);
            if (newLoginDt < oldLoginDt) {
                console.error(
                    "[ATTENDANCE LOGIN] BLOCKED: new login < old login for",
                    employee_id, "old:", oldLoginDt, "new:", newLoginDt
                );
                return res.status(400).json({
                    success: false,
                    message: "New login time cannot be before previous login time",
                });
            }

            // Step 1: Update logout_time first (new login time becomes logout time for previous session)
            await pool.query(
                `UPDATE attendance
                 SET logout_time = $1
                 WHERE id = $2`,
                [login_time, oldId]
            );

            // Step 2: Calculate total_hours using column-to-column subtraction
            await pool.query(
                `UPDATE attendance
                 SET total_hours = logout_time - login_time
                 WHERE id = $1`,
                [oldId]
            );

            console.log("[ATTENDANCE LOGIN] Closed old record:", oldId);
        }

        // Insert new attendance record
        const result = await pool.query(
            `INSERT INTO attendance (employee_id, login_time)
             VALUES ($1, $2)
             RETURNING id`,
            [employee_id, login_time]
        );

        return res.status(200).json({
            success: true,
            id:      result.rows[0].id,
        });

    } catch (error) {
        console.error("[ATTENDANCE LOGIN ERROR]", error.message);
        return res.status(500).json({
            success: false,
            message: error.message,
        });
    }
};

// ── POST /attendance/logout ───────────────────────────────────
exports.logoutAttendance = async (req, res) => {
    // ✅ Token se employee_id
    const employee_id = req.user.employee_id;

    // Parse timestamp from client - keep original format for PostgreSQL compatibility
    // ISO8601 with timezone info causes issues with timestamp without time zone
    let logout_time;
    const rawLogoutTime = req.body.logout_time;
    if (rawLogoutTime) {
        // Use raw value directly - PostgreSQL handles the format
        // Removing timezone conversion to preserve consistency with stored login_time
        logout_time = rawLogoutTime;
    } else {
        logout_time = new Date().toISOString();
    }

    console.log("[ATTENDANCE LOGOUT]", employee_id, "raw logout_time:", logout_time);

    // Get open record for validation
    const openRecord = await pool.query(
        `SELECT id, login_time FROM attendance
         WHERE employee_id = $1
           AND logout_time IS NULL
         ORDER BY id DESC LIMIT 1`,
        [employee_id]
    );

    if (openRecord.rows.length === 0) {
        console.warn("[ATTENDANCE LOGOUT] No open record:", employee_id);
        return res.status(404).json({
            success: false,
            message: "No open attendance record found",
        });
    }

    const openId = openRecord.rows[0].id;
    const loginDt = new Date(openRecord.rows[0].login_time);
    const logoutDt = new Date(logout_time);

    // 🚫 BLOCK invalid attendance: logout_time < login_time
    if (logoutDt < loginDt) {
        console.error(
            "[ATTENDANCE LOGOUT] BLOCKED: logout < login for",
            employee_id, "login:", loginDt, "logout:", logoutDt
        );
        return res.status(400).json({
            success: false,
            message: "Logout time cannot be before login time",
        });
    }

    try {
        // Step 1: Update logout_time first
        console.log("[ATTENDANCE LOGOUT] Step1: id=%s logout=%s", openId, logout_time);
        const updateResult = await pool.query(
            `UPDATE attendance
             SET logout_time = $1
             WHERE id = $2
             RETURNING id, logout_time, login_time`,
            [logout_time, openId]
        );

        console.log(
            "[ATTENDANCE LOGOUT] Step1 result: rowCount=%s data=%j",
            updateResult.rowCount, updateResult.rows[0]
        );

        if (updateResult.rowCount === 0) {
            throw new Error("No rows updated in step 1");
        }

        // Verify row has logout_time set
        const afterStep1 = await pool.query(
            `SELECT id, login_time, logout_time FROM attendance WHERE id = $1`,
            [openId]
        );
        console.log("[ATTENDANCE LOGOUT] After Step1: %j", afterStep1.rows[0]);

        // Step 2: Calculate total_hours using column-to-column subtraction
        // This avoids the $1::timestamp casting issue
        console.log("[ATTENDANCE LOGOUT] Step2: calculating total_hours for id=%s", openId);
        const calcResult = await pool.query(
            `UPDATE attendance
             SET total_hours = logout_time - login_time
             WHERE id = $1
             RETURNING id, total_hours`,
            [openId]
        );

        console.log(
            "[ATTENDANCE LOGOUT] Step2 result: rowCount=%s total_hours=%j",
            calcResult.rowCount, calcResult.rows[0]
        );

        if (calcResult.rowCount === 0) {
            throw new Error("No rows updated in step 2");
        }

        // Critical: Verify total_hours after calculate
        const afterCalc = await pool.query(
            `SELECT id, login_time, logout_time, total_hours
             FROM attendance WHERE id = $1`,
            [openId]
        );
        console.log("[ATTENDANCE LOGOUT] After Calculation: %j", afterCalc.rows[0]);

        if (!afterCalc.rows[0].total_hours) {
            console.error(
                "[ATTENDANCE LOGOUT] CRITICAL: total_hours is NULL after calculation!",
                afterCalc.rows[0]
            );
        }

        return res.status(200).json({ success: true });

    } catch (error) {
        console.error("[ATTENDANCE LOGOUT ERROR]", error.message, error.stack);
        return res.status(500).json({
            success: false,
            message: error.message,
        });
    }
};