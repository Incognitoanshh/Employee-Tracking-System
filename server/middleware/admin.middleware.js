/**
 * Admin-only middleware — role check karo JWT se
 */
const adminOnly = (req, res, next) => {
    if (req.employee?.role !== "admin") {
        return res.status(403).json({
            success: false,
            message: "Admin access required."
        });
    }
    next();
};

module.exports = { adminOnly };
