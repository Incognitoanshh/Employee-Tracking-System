const jwt = require("jsonwebtoken");

const verifyToken = (req, res, next) => {

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
