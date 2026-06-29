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

const isProduction = process.env.NODE_ENV === "production";

const allowedOrigin = process.env.ALLOWED_ORIGIN;
if (isProduction && !allowedOrigin) {
    console.error("❌ ALLOWED_ORIGIN is required in production (refusing wildcard CORS). ");
    process.exit(1);
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
        res.status(500).json({ success: false, message: "Database error", error: error.message });
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

app.use((req, res) => {
    res.status(404).json({ success: false, message: `Route not found: ${req.method} ${req.url}` });
});

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
            // Avoid logging huge bodies; keep it best-effort
            body: req && req.body ? req.body : undefined,
        });

        // Normalize message for clients
        const message = err && err.message ? err.message : "Internal server error";

        // If express.json() failed to parse, return 400 with explicit error message
        if (status === 400) {
            return res.status(400).json({ success: false, message });
        }

        return res.status(status).json({ success: false, message });
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

setInterval(() => {
    console.log(`[ALIVE] ${new Date().toISOString()}`);
}, 10000);
