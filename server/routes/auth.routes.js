const express = require("express");
const rateLimit = require("express-rate-limit");

const router = express.Router();

const authController = require(
    "../controllers/auth.controller"
);

// ─────────────────────────────────────────────────────────────────────────────
//  BUG FIX (production lockout risk):
//
//  Pehle sirf EK limiter tha: 10 login attempts / 15 min / IP.
//  express-rate-limit IP ke hisaab se count karta hai — aur ek company ke
//  saare employees ek hi office NAT ke peechhe, EK hi public IP se aate hain.
//  Matlab: subah 9 baje 10 employees login kar lein, aur 11th employee ko
//  15 minute ke liye "Too many login attempts" mil jaata — chahe usne apna
//  password bilkul sahi daala ho. Bade office me ye har subah ek outage
//  banta, aur employees ko lagta app hi kharab hai.
//
//  Ab do alag layers:
//    1. PER-USERNAME (asli brute-force defence) — ek account pe 10 FAILED
//       attempts / 15 min. `skipSuccessfulRequests` ki wajah se successful
//       logins count hi nahi hote, is liye normal use kabhi limit nahi
//       chhuता. Attacker ek account pe password guess kare to 10 pe ruk
//       jaayega, chahe wo kitne bhi IPs se aaye.
//    2. PER-IP (sirf DoS ceiling) — ek network se 300 requests / 15 min.
//       Itna ooncha ki poora office aaram se login kare, lekin ek machine
//       se automated flood phir bhi ruke.
//
//  NOTE: default MemoryStore process-local hai. ecosystem.config.js me
//  `instances: 1` hai is liye abhi theek hai — agar kabhi cluster mode pe
//  jao to shared store (Redis) chahiye hoga, warna har worker ka apna
//  alag counter hoga.
// ─────────────────────────────────────────────────────────────────────────────

// Layer 1 — asli brute-force protection, username pe keyed.
const loginUserLimiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    limit: 10,
    standardHeaders: true,
    legacyHeaders: false,
    // Sirf FAILED attempts count karo — roz ka normal login kabhi limit
    // ke paas bhi nahi pahunchega.
    skipSuccessfulRequests: true,
    keyGenerator: (req) =>
        `user:${String(req.body?.username || "").trim().toLowerCase() || "unknown"}`,
    // keyGenerator IP return nahi karta (hamesha username), is liye library
    // ka IPv6-normalisation check yahan lagu nahi hota.
    validate: { keyGeneratorIpFallback: false },
    message: {
        success: false,
        message: "Too many failed login attempts for this account, try after 15 minutes",
    },
});

// Layer 2 — network-level flood ceiling. Poore office ke liye generous.
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
    // BUG FIX: 100/15min bhi shared office IP pe kam tha — /refresh har
    // client apne auto-login pe maarta hai, aur /logout har session end pe.
    // 50 employees ka office in dono ko aaram se 100 ke paar le jaata.
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

module.exports = router;