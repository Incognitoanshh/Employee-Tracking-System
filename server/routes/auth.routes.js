const express = require("express");
const rateLimit = require("express-rate-limit");

const router = express.Router();

const authController = require(
    "../controllers/auth.controller"
);
const { verifyToken } = require("../middleware/auth.middleware");

// ─────────────────────────────────────────────────────────────────────────────
//  BUG FIX (production lockout risk):
//  There used to be a single limiter: 10 login attempts / 15 min / IP.
//  express-rate-limit counts per IP — and every employee in a company sits
//  behind the same office NAT, i.e. one public IP.
//
//  So: ten people log in at 9am, and the eleventh gets "Too many login
//  attempts" for 15 minutes even with the correct password. In a large
//  office that is an outage every morning, and it looks to staff like the
//  app itself is broken.
//
//  Now there are two separate layers:
//    1. PER-USERNAME (the real brute-force defence) — 10 FAILED attempts
//       per account / 15 min. `skipSuccessfulRequests` means successful
//       logins are not counted at all, so normal use never approaches the
//       limit. An attacker guessing one account's password is stopped at
//       10 attempts no matter how many IPs they come from.
//    2. PER-IP (a DoS ceiling only) — 300 requests / 15 min per network.
//       High enough for a whole office to log in comfortably, low enough
//       to stop an automated flood from a single machine.
//
//  NOTE: the default MemoryStore is process-local. ecosystem.config.js sets
//  `instances: 1`, so this is fine today — moving to cluster mode would
//  need a shared store (Redis), otherwise each worker keeps its own count.
// ─────────────────────────────────────────────────────────────────────────────

// Layer 1 — the real brute-force protection, keyed on username.
const loginUserLimiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    limit: 10,
    standardHeaders: true,
    legacyHeaders: false,
    // Count only FAILED attempts — ordinary daily logins never come
    // anywhere near the limit.
    skipSuccessfulRequests: true,
    keyGenerator: (req) =>
        `user:${String(req.body?.username || "").trim().toLowerCase() || "unknown"}`,
    // keyGenerator never returns an IP (always the username), so the
    // library's IPv6-normalisation check does not apply here.
    validate: { keyGeneratorIpFallback: false },
    message: {
        success: false,
        message: "Too many failed login attempts for this account, try after 15 minutes",
    },
});

// Layer 2 — network-level flood ceiling. Generous enough for a whole office.
const loginIpLimiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    limit: 300,
    standardHeaders: true,
    legacyHeaders: false,
    message: {
        success: false,
        message: "Too many requests from this network, please try again later",
    },
});

const generalLimiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    // BUG FIX: even 100/15min was too low for a shared office IP —
    // every client hits /refresh on auto-login and /logout at session end.
    // An office of 50 pushes past 100 without trying.
    limit: 1000,
    standardHeaders: true,
    legacyHeaders: false,
    message: { success: false, message: "Too many requests, slow down" }
});

router.post(
    "/login",
    loginIpLimiter,
    loginUserLimiter,
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

// Changing your own password needs the current one, which makes this
// endpoint another place to guess it. Keyed on the account rather than the
// IP for the same reason as login: a whole office shares one public IP, and
// one person changing their password must not lock out everyone else.
const passwordChangeLimiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    limit: 10,
    standardHeaders: true,
    legacyHeaders: false,
    skipSuccessfulRequests: true,
    keyGenerator: (req) => `pwd:${req.employee?.employee_id || "unknown"}`,
    validate: { keyGeneratorIpFallback: false },
    message: {
        success: false,
        message: "Too many password attempts for this account, try after 15 minutes",
    },
});

router.post(
    "/password",
    verifyToken,
    passwordChangeLimiter,
    authController.changePassword
);

module.exports = router;