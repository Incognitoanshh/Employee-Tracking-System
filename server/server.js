require("dotenv").config();

const express        = require("express");
const cors           = require("cors");
const pool           = require("./config/db");
const { verifyToken } = require("./middleware/auth.middleware");
const authRoutes       = require("./routes/auth.routes");
const screenshotRoutes = require("./routes/screenshot.routes");
const logRoutes        = require("./routes/log.routes");
const dashboardRoutes  = require("./routes/dashboard.routes");
const configRoutes     = require("./routes/config.routes");
const adminRoutes      = require("./routes/admin.routes");
const attendanceRoutes = require("./routes/attendance.routes");
const chatRoutes       = require("./routes/chat.routes");

// Startup env validation
const REQUIRED_ENV = ["JWT_SECRET", "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"];
const missingEnv = REQUIRED_ENV.filter((key) => !process.env[key]);
if (missingEnv.length > 0) {
    console.error(`❌ Missing required environment variables: ${missingEnv.join(", ")}`);
    process.exit(1);
}

pool.query("SELECT NOW()")
    .then(result => console.log("✅ DB CONNECTED:", result.rows[0]))
    .catch(error => console.error("❌ DB CONNECTION FAILED:", error.message));

const app = express();

// SECURITY: `X-Powered-By: Express` header hata do — ye attacker ko free me
// bata deta hai ki backend kis stack pe hai (targeted CVE scanning aasan ho
// jaata hai). Koi functional asar nahi.
app.disable("x-powered-by");

const isProduction = process.env.NODE_ENV === "production";

const allowedOrigin = process.env.ALLOWED_ORIGIN;
if (isProduction && !allowedOrigin) {
    console.error("❌ ALLOWED_ORIGIN is required in production (refusing wildcard CORS). ");
    process.exit(1);
}

// ── TRUST PROXY ──────────────────────────────────────────────────────────
// Express reads the client address from the socket unless told otherwise.
// Put nginx in front and every request arrives from 127.0.0.1, so the
// per-IP rate limiter in auth.routes.js buckets the ENTIRE company into a
// single 300-requests/15-min budget — a thousand employees exhaust that in
// seconds and nobody can log in. That is precisely the office-wide lockout
// the two-layer limiter was written to prevent, reintroduced by the proxy.
//
// This is NOT unconditional, because trusting X-Forwarded-For while port
// 8000 is still reachable from the internet lets an attacker forge that
// header and get a fresh rate-limit bucket per request, defeating the
// limiter from the other direction. Both behaviours were verified.
//
// So: enable TRUST_PROXY=1 in .env at the same time as putting nginx in
// front AND firewalling port 8000 to localhost. Not before.
const trustProxy = process.env.TRUST_PROXY;
if (trustProxy) {
    // A number means "trust this many hops", which is what one nginx in
    // front needs. `true` would trust any forwarded chain, including a
    // forged one.
    const hops = Number.parseInt(trustProxy, 10);
    app.set("trust proxy", Number.isNaN(hops) ? 1 : hops);
    console.log(`[BOOT] trust proxy enabled (${Number.isNaN(hops) ? 1 : hops} hop)`);
}

app.use(cors({
    // Dev: keep permissive behavior unless operator provided a stricter value.
    // Prod: wildcard is blocked above; only explicit origin allowed.
    origin: allowedOrigin || "*",
    methods: ["GET", "POST", "PUT", "DELETE"],
    allowedHeaders: ["Content-Type", "Authorization"],
}));

app.use(express.json());

app.use((req, res, next) => {
    // Avoid logging query strings (may contain sensitive data).
    console.log(`[${new Date().toISOString()}] ${req.method} ${req.path}`);
    next();
});


// Health check — SABSE PEHLE register karo, kisi bhi route se pehle
app.get("/api/health", async (req, res) => {
    try {
        const result = await pool.query("SELECT NOW()");
        res.json({
            success:  true,
            status:   "healthy",
            database: "connected",
            time:     result.rows[0].now,
            uptime:   process.uptime(),
        });
    } catch (error) {
        res.status(500).json({ success: false, status: "unhealthy", error: error.message });
    }
});

app.get("/", async (req, res) => {
    try {
        const result = await pool.query("SELECT NOW()");
        res.json({ success: true, message: "ETS Server is running", database: "connected", time: result.rows[0] });
    } catch (error) {
        // The message is NOT returned. This route is unauthenticated — it is
        // what a health check hits — and a database error message names the
        // host, the database and sometimes the user it failed to connect as.
        console.error("[500] GET /", error.message);
        res.status(500).json({ success: false, message: "Database error" });
    }
});

// API Routes
app.use("/api/auth",        authRoutes);
app.use("/api/screenshots", verifyToken, screenshotRoutes);
app.use("/api/logs",        verifyToken, logRoutes);
app.use("/api/dashboard",   verifyToken, dashboardRoutes);
app.use("/api/config",      verifyToken, configRoutes);
app.use("/api/admin",       verifyToken, adminRoutes);
app.use("/api/attendance",  verifyToken, attendanceRoutes);
app.use("/api/chat",        verifyToken, chatRoutes);

app.use((req, res) => {
    res.status(404).json({ success: false, message: `Route not found: ${req.method} ${req.url}` });
});

/**
 * A request body with the secrets taken out, for logging.
 *
 * Names rather than routes: the same field can arrive at more than one
 * endpoint, and a route-by-route list is a list somebody forgets to add to.
 * Recursive because a password can sit one level down — `{ employee: { ... } }`.
 */
const SECRET_FIELDS = new Set([
    "password", "new_password", "old_password", "current_password",
    "confirm_password", "token", "auth_token", "refresh_token",
    "jwt", "secret", "encryption_key", "api_key",
]);

function redactForLog(value, depth = 0) {
    if (!value || typeof value !== "object" || depth > 4) return value;
    if (Array.isArray(value)) return value.map((item) => redactForLog(item, depth + 1));
    const safe = {};
    for (const [key, entry] of Object.entries(value)) {
        safe[key] = SECRET_FIELDS.has(String(key).toLowerCase())
            ? "[redacted]"
            : redactForLog(entry, depth + 1);
    }
    return safe;
}

// Advanced error formatter (must be the LAST middleware before app.listen)
app.use((err, req, res, next) => {
    try {
        const status = err.status || err.statusCode || (err.type === "entity.parse.failed" ? 400 : 500);

        // Log full stack trace to terminal
        console.error("[ERROR]", {
            status,
            message: err && err.message,
            stack: err && err.stack ? err.stack : err,
            route: req && (req.method + " " + req.originalUrl),
            // REDACTED. The body is genuinely useful for working out what a
            // failing request contained — except that on /auth/login it
            // contains the password in the clear, and these lines go to PM2's
            // log files and stay there. Any error thrown anywhere downstream
            // of a parsed login body wrote a working password to disk.
            body: redactForLog(req && req.body),
        });

        // A 4xx is the caller's fault, so telling them what was wrong with
        // their request is useful. A 5xx is ours, and err.message there is
        // an internal detail — an unauthenticated caller was being handed
        // things like "Cannot destructure property 'username' of 'req.body'",
        // which describes our source code. Log it, do not return it.
        if (status < 500) {
            const message = err && err.message ? err.message : "Bad request";
            return res.status(status).json({ success: false, message });
        }

        return res.status(status).json({
            success: false,
            message: "Internal server error",
        });
    } catch (e) {
        // Fallback: never let the error handler crash PM2
        console.error("[ERROR_HANDLER_CRASH]", e);
        return res.status(500).json({ success: false, message: "Internal server error" });
    }
});


const PORT = process.env.PORT || 8000;
const server = app.listen(PORT, () => {
    console.log(`🚀 ETS Backend running on port ${PORT}`);
});

const shutdown = (signal) => {
    console.log(`[${signal}] Graceful shutdown initiated...`);
    server.close(() => {
        pool.end(() => {
            console.log("DB pool closed. Exiting.");
            process.exit(0);
        });
    });
    setTimeout(() => { process.exit(1); }, 10000);
};

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT",  () => shutdown("SIGINT"));

process.on("uncaughtException", (err) => {
    console.error("UNCAUGHT EXCEPTION:", err);
    shutdown("uncaughtException");
});

process.on("unhandledRejection", (reason) => {
    console.error("UNHANDLED REJECTION:", reason);
});

// `unref` so this heartbeat never becomes the reason the process stays up.
// The listening socket already keeps the server alive; without unref an
// integration test that closes the server would hang here forever instead
// of exiting.
setInterval(() => {
    console.log(`[ALIVE] ${new Date().toISOString()}`);
}, 10000).unref();

// Exported so tests can start the real app against a scratch database and
// shut it down again. Nothing here runs differently when the server is
// started normally with `node server.js`.
module.exports = { app, server, pool };
