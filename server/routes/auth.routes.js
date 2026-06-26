const express   = require("express");
const rateLimit = require("express-rate-limit");

const router         = express.Router();
const authController = require("../controllers/auth.controller");
const { verifyToken } = require("../middleware/auth.middleware");

// ✅ Login rate limiter — 10 attempts / 15 min
const loginLimiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    max:      10,
    message:  {
        success: false,
        message: "Too many login attempts — try after 15 minutes",
    },
});

router.post("/login",  loginLimiter, authController.login);

// ✅ Logout — token verify karo
router.post("/logout", verifyToken,  authController.logout);

// ✅ Heartbeat — keep session alive
router.post("/heartbeat", verifyToken, authController.heartbeat);

module.exports = router;