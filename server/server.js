require("dotenv").config();

const dashboardRoutes = require("./routes/dashboard.routes");
const express = require("express");
const cors = require("cors");
const pool = require("./config/db");
const authRoutes = require("./routes/auth.routes");
const screenshotRoutes = require("./routes/screenshot.routes");
const logRoutes = require("./routes/log.routes");
const { verifyToken } = require("./middleware/auth.middleware"); // ✅ FIX: import karo

pool.query("SELECT NOW()")
    .then(result => {
        console.log("DB CONNECTED");
        console.log(result.rows[0]);
    })
    .catch(error => {
        console.log("DB ERROR");
        console.log(error);
    });

const app = express();

app.use(cors());
app.use(express.json());

app.use((req, res, next) => {
    console.log("REQUEST RECEIVED:", req.method, req.url);
    next();
});

app.use("/api/auth", authRoutes);                               // login — public rehna chahiye
app.use("/api/screenshots", verifyToken, screenshotRoutes);    // ✅ FIX: protected
app.use("/api/logs", verifyToken, logRoutes);                  // ✅ FIX: protected
app.use("/api/dashboard", verifyToken, dashboardRoutes);       // ✅ FIX: protected

app.get("/", async (req, res) => {
    try {
        const result = await pool.query("SELECT NOW()");
        res.json({
            success: true,
            database: "connected",
            time: result.rows[0]
        });
    } catch (error) {
        res.status(500).json({
            success: false,
            error: error.message
        });
    }
});

const PORT = process.env.PORT || 5000;

app.listen(PORT, () => {
    console.log(`ETS Backend Running On Port ${PORT}`);
});

setInterval(() => {
    console.log("SERVER ALIVE");
}, 10000);