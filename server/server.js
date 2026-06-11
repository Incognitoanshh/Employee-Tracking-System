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

pool.query("SELECT NOW()")
    .then(result => {
        console.log("✅ DB CONNECTED:", result.rows[0]);
    })
    .catch(error => {
        console.error("❌ DB CONNECTION FAILED:", error.message);
    });

const app = express();

app.use(cors());
app.use(express.json());


app.use((req, res, next) => {
    console.log(`[${new Date().toISOString()}] ${req.method} ${req.url}`);
    next();
});

app.use("/api/auth",        authRoutes);                        
app.use("/api/screenshots", verifyToken, screenshotRoutes);
app.use("/api/logs",        verifyToken, logRoutes);       
app.use("/api/dashboard",   verifyToken, dashboardRoutes);  
app.use("/api/config",      verifyToken, configRoutes);
app.use("/api/admin",       verifyToken, adminRoutes);

app.get("/", async (req, res) => {
    try {
        const result = await pool.query("SELECT NOW()");
        res.json({
            success:  true,
            message:  "ETS Server is running",
            database: "connected",
            time:     result.rows[0]
        });
    } catch (error) {
        res.status(500).json({
            success:  false,
            message:  "Database error",
            error:    error.message
        });
    }
});


app.use((req, res) => {
    res.status(404).json({
        success: false,
        message: `Route not found: ${req.method} ${req.url}`
    });
});

app.use((err, req, res, next) => {
    console.error("UNHANDLED ERROR:", err);
    res.status(500).json({
        success: false,
        message: "Internal server error"
    });
});

const PORT = process.env.PORT || 5000;

app.listen(PORT, () => {
    console.log(`🚀 ETS Backend running on port ${PORT}`);
});

setInterval(() => {
    console.log(`[ALIVE] ${new Date().toISOString()}`);
}, 10000);
