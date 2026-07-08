const express = require("express");
const rateLimit = require("express-rate-limit");

const router = express.Router();

const authController = require(
    "../controllers/auth.controller"
);

const loginLimiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    max: 10,
    message: { success: false, message: "Too many login attempts, try after 15 minutes" }
});

const generalLimiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    max: 100,
    message: { success: false, message: "Too many requests, slow down" }
});

router.post(
    "/login",
    loginLimiter,
    authController.login
);

router.post(
    "/refresh",
    generalLimiter,
    authController.refresh
);

router.post(
    "/logout",
    generalLimiter,
    authController.logout
);

module.exports = router;