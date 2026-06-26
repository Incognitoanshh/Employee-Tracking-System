const jwt  = require("jsonwebtoken");
const pool = require("../config/db");

exports.verifyToken = async (req, res, next) => {
    const authHeader = req.headers["authorization"];

    if (!authHeader || !authHeader.startsWith("Bearer ")) {
        return res.status(401).json({
            success: false,
            message: "No token provided",
        });
    }

    const token = authHeader.split(" ")[1];

    try {
        const decoded = jwt.verify(token, process.env.JWT_SECRET);

        const session = await pool.query(
            `SELECT employee_id FROM active_sessions WHERE employee_id = $1`,
            [decoded.employee_id]
        );

        if (session.rows.length === 0) {
            return res.status(401).json({
                success: false,
                message: "Session expired or logged out",
            });
        }

        req.user = {
            employee_id: decoded.employee_id,
            role:        decoded.role,
        };

        next();

    } catch (err) {
        if (err.name === "TokenExpiredError") {
            return res.status(401).json({
                success: false,
                message: "Token expired — please login again",
            });
        }
        return res.status(401).json({
            success: false,
            message: "Invalid token",
        });
    }
};