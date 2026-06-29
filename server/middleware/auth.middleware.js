const pool = require("../config/db");
const jwt = require("jsonwebtoken");

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

        const session = await pool.query(
            `SELECT token FROM active_sessions WHERE employee_id = $1`,
            [decoded.employee_id]
        );

        if (session.rows.length > 0 && session.rows[0].token !== token) {
            return res.status(401).json({
                success: false,
                message: "Session expired. Logged in from another device."
            });
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
