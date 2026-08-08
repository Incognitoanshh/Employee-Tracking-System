const pool = require("../config/db");
const jwt = require("jsonwebtoken");

// When each employee's last_seen was last written. In-process only, and
// that is fine: it is a throttle, not state. A restart just means one extra
// write per employee.
const lastSeenWrites = new Map();

const verifyToken = async (req, res, next) => {

    const authHeader = req.headers["authorization"];
    const token = authHeader && authHeader.split(" ")[1];

    if (!token) {
        return res.status(401).json({
            success: false,
            message: "Access denied. No token provided."
        });
    }

    try {
        const decoded = jwt.verify(token, process.env.JWT_SECRET);

        // Suspension and session in ONE query. Both are needed on every
        // authenticated request, and this is the hottest path in the system —
        // a thousand clients polling every five seconds is two hundred
        // requests a second, so an extra round trip here is two hundred extra
        // round trips a second for nothing.
        //
        // Suspension is checked on every request rather than only at login:
        // suspending clears the session too, but a token already in flight
        // would otherwise keep working, and a suspension that takes effect
        // eventually is not a suspension.
        const state = await pool.query(
            `SELECT e.suspended,
                    s.token,
                    (s.employee_id IS NOT NULL) AS has_session
               FROM employees e
               LEFT JOIN active_sessions s ON s.employee_id = e.employee_id
              WHERE e.employee_id = $1`,
            [decoded.employee_id]
        );

        // NO ROW AT ALL MEANS THE ACCOUNT IS GONE, and that must end the
        // request here.
        //
        // BUG this fixes: the query starts FROM employees, so a deleted
        // account returned nothing — which made `suspended` undefined and
        // `hasSession` false, so BOTH checks below were skipped and the
        // request went through on the strength of the JWT alone. A deleted
        // employee kept full access until their token expired, up to
        // twenty-four hours, and a deleted ADMIN stayed an admin, because
        // the role is carried in the token rather than read from the row
        // that no longer exists.
        //
        // Deleting somebody is the one action an administrator takes
        // expecting it to be immediate.
        if (state.rows.length === 0) {
            return res.status(401).json({
                success: false,
                message: "This account no longer exists.",
            });
        }

        if (state.rows[0]?.suspended === true) {
            return res.status(403).json({
                success: false,
                suspended: true,
                message: "You are suspended. Contact your administrator.",
            });
        }

        // A NULL token means the row EXISTS and the session was ended —
        // logout, password reset, force logout and suspend all clear it that
        // way, and every one of them must reject the old token. No row at all
        // is a different thing and is not a mismatch.
        //
        // BUG this nearly shipped: folding the two queries into one, an
        // earlier version treated a NULL token as "no session row" and
        // skipped the check entirely — so a token cleared by a password reset
        // kept working. test_password caught it on the reset path.
        const hasSession = state.rows[0]?.has_session === true;

        if (hasSession && state.rows[0].token !== token) {
            return res.status(401).json({
                success: false,
                message: "Session expired. Logged in from another device."
            });
        }

        // Stamp the session as alive, so login can tell a client that is
        // actually running from one whose machine was closed without logging
        // out. Throttled to once a minute: clients poll every five seconds,
        // and a write per request would be a write per employee per five
        // seconds for no extra accuracy.
        //
        // Deliberately not awaited — this must never add latency to a real
        // request, and a failure here is not a reason to reject one.
        const now = Date.now();
        if (!lastSeenWrites.has(decoded.employee_id) ||
            now - lastSeenWrites.get(decoded.employee_id) > 60000) {
            lastSeenWrites.set(decoded.employee_id, now);
            pool.query(
                `UPDATE active_sessions SET last_seen = NOW() WHERE employee_id = $1`,
                [decoded.employee_id]
            ).catch(() => {});
        }

        req.employee = decoded;
        next();
    } catch (error) {
        return res.status(403).json({
            success: false,
            message: "Invalid or expired token."
        });
    }

};

module.exports = { verifyToken };
