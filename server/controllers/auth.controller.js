const pool = require("../config/db");
const { endSession, markLoggedIn } = require("../utils/session");
const jwt = require("jsonwebtoken");
const bcrypt = require("bcryptjs");
const { validatePassword } = require("../utils/password_policy");

/**
 * How long a session may go unheard before a new login may take it over.
 *
 * The client polls every few seconds and stamps last_seen at most once a
 * minute, backing off to two when the network is failing. Two minutes is
 * therefore "this machine is gone", not "this machine is busy".
 */
const LOGIN_STALE_MINUTES = 2;

exports.logout = async (req, res) => {
    const authHeader = req.headers["authorization"];
    const token = authHeader && authHeader.split(" ")[1];
    if (!token) return res.status(400).json({ success: false, message: "No token provided" });

    // Best-effort decode (signature/expiry verify zaroori nahi — agar token
    // already expire ho chuka hai to bhi employee_id nikal ke uski
    // active_session clear kar do; logout ka intent already achieve hai).
    const decoded = jwt.decode(token);
    if (decoded && decoded.employee_id) {
        try {
            // token = NULL karo, row DELETE nahi — verifyToken middleware
            // sirf tab hi purane token ko reject karta hai jab active_sessions
            // row EXIST kare aur token mismatch ho. Row delete karne se check
            // hi skip ho jata (koi row hi nahi milta), purana token tab bhi
            // apni natural 24h expiry tak chalta rehta — security gap.
            await endSession(pool, decoded.employee_id);
        } catch (dbError) {
            // DB issue — client-side session already clear ho chuki hogi,
            // logout request ko fail mat karo iske liye.
        }
    }

    return res.json({ success: true, message: "Logged out" });
};

/**
 * POST /api/auth/refresh
 *
 * Extend a session that is STILL A SESSION.
 *
 * THREE THINGS THIS USED TO GET WRONG, all of them silently.
 *
 * 1. FORCE LOGOUT DID NOT WORK. Signing somebody out sets their stored token
 *    to NULL. This route only checked that the presented JWT was
 *    cryptographically valid — which it still is, for the rest of its 24
 *    hours — and then wrote a fresh token straight back into the row. The
 *    client refreshes on its own, so an administrator would force a logout,
 *    watch it take effect, and the app would let itself back in seconds
 *    later. Measured end to end, not reasoned about.
 *
 * 2. A SUSPENDED ACCOUNT WAS STILL ISSUED TOKENS. The middleware blocks the
 *    request afterwards, so nothing was reachable — but handing a live
 *    credential to an account somebody has just disabled is not something to
 *    leave to a check further down the line.
 *
 * 3. NO ROW MEANT NO SESSION, AND NO COMPLAINT. `UPDATE ... WHERE
 *    employee_id` matches nothing when the row is gone, so the caller got a
 *    working token that was recorded nowhere. That account then had no
 *    session at all: it read as offline in the panel while plainly working,
 *    and the single-machine rule had nothing left to compare against.
 *
 * So the presented token must now BE the session — matching the stored one
 * exactly. Anything else ends here.
 */
exports.refresh = async (req, res) => {
    const authHeader = req.headers["authorization"];
    const token = authHeader && authHeader.split(" ")[1];
    if (!token) return res.status(401).json({ success: false, message: "No token provided" });

    try {
        const decoded = jwt.verify(token, process.env.JWT_SECRET);

        const held = await pool.query(
            `SELECT s.token, e.suspended
               FROM active_sessions s
               JOIN employees e ON e.employee_id = s.employee_id
              WHERE s.employee_id = $1`,
            [decoded.employee_id]
        );

        // No row, no stored token, or a different one: this is not a live
        // session, whatever the JWT still says about itself.
        if (held.rows.length === 0 || !held.rows[0].token || held.rows[0].token !== token) {
            return res.status(403).json({
                success: false,
                message: "Session ended. Please log in again.",
            });
        }
        if (held.rows[0].suspended) {
            return res.status(403).json({
                success: false,
                message: "This account is suspended.",
            });
        }

        const newToken = jwt.sign(
            { employee_id: decoded.employee_id, role: decoded.role },
            process.env.JWT_SECRET,
            { expiresIn: "24h" }
        );
        // The row is known to exist, so this cannot silently match nothing.
        // last_seen is stamped as well: a refresh is the app saying it is
        // running, which is exactly what presence asks about.
        await pool.query(
            `UPDATE active_sessions
                SET token = $1, login_time = NOW(), last_seen = NOW()
              WHERE employee_id = $2`,
            [newToken, decoded.employee_id]
        );
        return res.json({ success: true, token: newToken });
    } catch (error) {
        return res.status(403).json({ success: false, message: "Invalid or expired token" });
    }
};

exports.login = async (req, res) => {
    const { username, password } = req.body || {};

    if (!username || !password) {
        return res.status(400).json({ success: false, message: "username and password are required" });
    }

    try {
        // Matched without regard to case. The super admin registered as
        // "Amazeinternet" could not sign in by typing "amazeinternet": the
        // password was right and the only feedback was "Invalid credentials",
        // which reads as a wrong password and sends people round in circles.
        //
        // Safe only because employees_username_lower_idx makes two accounts
        // differing by case impossible — otherwise this would match more than
        // one row and let a lookalike account take over a real one.
        //
        // Uses the same LOWER(username) expression as that index so the
        // lookup stays indexed rather than scanning the table.
        const result = await pool.query(
            "SELECT * FROM employees WHERE LOWER(username) = LOWER($1)",
            [String(username).trim()]
        );

        if (result.rows.length === 0) {
            return res.status(401).json({ success: false, message: "Invalid credentials" });
        }

        const employee = result.rows[0];

        const isMatch = await bcrypt.compare(password, employee.password);
        if (!isMatch) {
            return res.status(401).json({ success: false, message: "Invalid credentials" });
        }

        // Checked AFTER the password, deliberately. Telling an unauthenticated
        // caller "that account is suspended" confirms the account exists and
        // hints at its state to anyone guessing usernames. Someone who has
        // just proved they own it is a different matter — they need to know
        // why they cannot get in, or they will keep trying and lock
        // themselves out.
        if (employee.suspended === true) {
            return res.status(403).json({
                success: false,
                suspended: true,
                message: "You are suspended. Contact your administrator.",
            });
        }

        // FIX: Login pe attendance close NAHI karo — employee online status ke liye
        // open attendance record rehna chahiye. attendance/login endpoint
        // apna open session khud close karke naya banata hai.

        // ── ONE ACCOUNT, ONE MACHINE ──────────────────────────────────
        //
        // Signing in used to take the account over and evict whoever held
        // it. Two people sharing a login could each work all day, quietly
        // throwing the other out, and attendance would read as one person.
        //
        // Refusing whenever a session row exists would lock people out of
        // their own accounts: a crash, a closed lid or a power cut never
        // logs out, so the row would sit there forever. The test is
        // therefore whether the session is ALIVE — last_seen is stamped by
        // every authenticated request, and clients poll every five seconds,
        // so anything quiet for two minutes is gone.
        //
        // An admin can always free a stuck account with Force logout, which
        // clears the token outright.
        // WHICH machine matters, not just whether one is signed in.
        //
        // BUG this fixes: the check asked only "is a session alive?", so
        // closing the app without signing out locked you out of your own
        // laptop. The session was still live, the login looked exactly like a
        // second machine, and it was refused — with a message telling you to
        // log out somewhere you were already sitting.
        //
        // The client has always sent device_id. Comparing it separates the
        // two cases the rule actually cares about: the same machine coming
        // back is fine, a second machine is not.
        const held = await pool.query(
            `SELECT token, last_seen, device_id,
                    (last_seen > NOW() - INTERVAL '${LOGIN_STALE_MINUTES} minutes')
                        AS still_breathing
               FROM active_sessions
              WHERE employee_id = $1 AND token IS NOT NULL`,
            [employee.employee_id]
        );

        const thisDevice = String(req.body?.device_id || "").trim() || null;

        if (held.rows.length > 0) {
            // The held token may simply have expired — a machine that was
            // closed a day ago should not hold the account forever. Verify
            // it; an expired or unreadable token means the session is over
            // whatever the row says.
            let stillValid = false;
            try {
                jwt.verify(held.rows[0].token, process.env.JWT_SECRET);
                stillValid = true;
            } catch (_) {
                stillValid = false;
            }

            // ONE LOGIN AT A TIME, WHATEVER THE MACHINE.
            //
            // This used to let the same device id take its own session back.
            // That is right when a company hands out the laptops; here people
            // use their own, and a device id changes on a reinstall, a new
            // machine or a wiped data directory — so somebody looked like a
            // second person and was locked out of their own account.
            //
            // What replaces it is the heartbeat. A session that has not been
            // heard from in LOGIN_STALE_MINUTES is not somebody working
            // elsewhere; it is a machine that crashed, ran out of battery or
            // was killed, and nothing calls logout for any of those. Without
            // this, a bare flag would lock that person out until an
            // administrator intervened — which on personal machines is a
            // support call a week.
            //
            // Two machines at once stays impossible: a live one reports in
            // every few seconds, so its session never goes stale.
            const abandoned = held.rows[0].still_breathing === false;

            if (stillValid && !abandoned) {
                // Say when that machine was last heard from. "Already logged
                // in" on its own leaves no way to tell somebody actually
                // working from a laptop that was shut without logging out —
                // and those need opposite responses.
                const seen = held.rows[0].last_seen
                    ? Math.round((Date.now() - new Date(held.rows[0].last_seen)) / 60000)
                    : null;
                let when = "";
                if (seen !== null) {
                    when = seen < 2 ? " It is active right now."
                         : seen < 60 ? ` It was last active ${seen} minutes ago.`
                         : ` It was last active ${Math.round(seen / 60)} hour(s) ago —`
                           + " that machine may simply have been switched off.";
                }
                return res.status(409).json({
                    success: false,
                    message: "This account is already signed in."
                           + when
                           + " Sign out there first, or ask an admin to force logout.",
                });
            }

            // Expired: release it so the next step can take over.
            await endSession(pool, employee.employee_id);
        }

        // FIX: 24h token expiry
        const token = jwt.sign(
            { employee_id: employee.employee_id, role: employee.role },
            process.env.JWT_SECRET,
            { expiresIn: "24h" }
        );

        // Reset force_logout on login
        await pool.query(
            `UPDATE employee_configs SET force_logout = false WHERE employee_id = $1`,
            [employee.employee_id]
        );

        // New active session
        await pool.query(
            `INSERT INTO active_sessions (employee_id, token, login_time, last_seen, device_id)
             VALUES ($1, $2, NOW(), NOW(), $3)
             ON CONFLICT (employee_id) DO UPDATE
                 SET token = $2, login_time = NOW(), last_seen = NOW(), device_id = $3`,
            [employee.employee_id, token, thisDevice]
        );
        // The flag, in step with the token it describes — see utils/session.
        await markLoggedIn(pool, employee.employee_id);

        // Employee config + shift fetch
        const configResult = await pool.query(
            `SELECT * FROM employee_configs WHERE employee_id = $1`,
            [employee.employee_id]
        );
        const globalConfig = await pool.query(
            `SELECT * FROM employee_configs WHERE employee_id IS NULL LIMIT 1`
        );
        const config = configResult.rows[0] || globalConfig.rows[0] || {};

        // shift_start/shift_end are stored in employee_configs and are HH:MM IST.
        const shiftStart = config.shift_start
            ? String(config.shift_start).substring(0, 5)
            : "09:00";
        const shiftEnd = config.shift_end
            ? String(config.shift_end).substring(0, 5)
            : "18:00";


        return res.status(200).json({
            success: true,
            employee_id: employee.employee_id,
            role: employee.role,
            // The client shows the change-password screen instead of the
            // panel while this is true, so an admin-issued temporary
            // password cannot become someone's permanent one.
            must_change_password: employee.must_change_password === true,
            // Naye employee panel ke header ke liye — naam na ho to username.
            full_name:   employee.full_name || employee.username,
            designation: employee.designation
                || (employee.role === "super_admin" ? "Administrator"
                    : employee.role === "admin" ? "Manager" : "Employee"),
            token,
            shift_start: shiftStart,
            shift_end:   shiftEnd,
            config: {
                screenshot_min_minutes:  config.screenshot_min_minutes  || 3,
                screenshot_max_minutes:  config.screenshot_max_minutes  || 10,
                screenshots_per_day:        config.screenshots_per_day        || 10,
                upload_interval_minutes: config.upload_interval_minutes || 60,
                idle_threshold_seconds:  config.idle_threshold_seconds  || 60,
                verbose_logging:         config.verbose_logging         || false,
            }
        });

    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Server error" });
    }
};

/**
 * An employee changes their own password.
 *
 * Requires the current password even though the caller already holds a valid
 * token: a token left behind on a machine someone else can reach must not be
 * enough to lock the real owner out of their own account.
 *
 * Every other device is signed out. `active_sessions` holds one token per
 * employee, so writing the new token here is what does it — anything
 * presenting the old one fails `verifyToken`'s mismatch check. The caller
 * gets that new token back so the device they are sitting at stays working;
 * without it, changing your password would immediately log you out of the
 * app you changed it in.
 */
exports.changePassword = async (req, res) => {
    const { current_password, new_password } = req.body || {};
    const employeeId = req.employee?.employee_id;

    if (!employeeId) {
        return res.status(401).json({ success: false, message: "Not authenticated" });
    }
    if (!current_password || !new_password) {
        return res.status(400).json({
            success: false,
            message: "current_password and new_password are required",
        });
    }

    try {
        const result = await pool.query(
            `SELECT employee_id, username, password, role FROM employees WHERE employee_id = $1`,
            [employeeId]
        );
        if (result.rows.length === 0) {
            return res.status(404).json({ success: false, message: "Account not found" });
        }
        const employee = result.rows[0];

        const isMatch = await bcrypt.compare(current_password, employee.password);
        if (!isMatch) {
            return res.status(401).json({
                success: false,
                message: "Current password is incorrect",
            });
        }

        const problem = validatePassword(new_password, {
            username:   employee.username,
            employeeId: employee.employee_id,
        });
        if (problem) {
            return res.status(400).json({ success: false, message: problem });
        }

        // Reusing the current password would leave `must_change_password`
        // cleared without anything actually changing, which defeats a reset.
        if (await bcrypt.compare(new_password, employee.password)) {
            return res.status(400).json({
                success: false,
                message: "New password must be different from the current one",
            });
        }

        const hashed = await bcrypt.hash(new_password, 10);
        await pool.query(
            `UPDATE employees
                SET password = $1, must_change_password = false, password_changed_at = NOW()
              WHERE employee_id = $2`,
            [hashed, employeeId]
        );

        const token = jwt.sign(
            { employee_id: employee.employee_id, role: employee.role },
            process.env.JWT_SECRET,
            { expiresIn: "24h" }
        );
        await pool.query(
            `INSERT INTO active_sessions (employee_id, token, login_time)
             VALUES ($1, $2, NOW())
             ON CONFLICT (employee_id) DO UPDATE SET token = $2, login_time = NOW()`,
            [employeeId, token]
        );

        await pool.query(
            `INSERT INTO activity_logs (employee_id, activity) VALUES ($1, $2)`,
            [employeeId, "PASSWORD CHANGED : by the account owner"]
        ).catch(() => {});

        return res.json({
            success: true,
            message: "Password changed. Other devices have been signed out.",
            token,
        });

    } catch (error) {
        console.error("[500]", req.method, req.originalUrl, error.message);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
};
