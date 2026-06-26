exports.requireAdmin = (req, res, next) => {
    if (!req.user) {
        return res.status(401).json({
            success: false,
            message: "Unauthorized — not logged in",
        });
    }

    if (req.user.role !== "admin") {
        return res.status(403).json({
            success: false,
            message: "Access denied — admin only",
        });
    }

    next();
};