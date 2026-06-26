require("dotenv").config();
const express = require("express");
const cors = require("cors");
const path = require("path");

const pool = require("./config/db");
const { verifyToken } = require("./middleware/auth.middleware");
const { helmet, limiter } = require("./middleware/security");

const authRoutes = require("./routes/auth.routes");
const screenshotRoutes = require("./routes/screenshot.routes");
const attendanceRoutes = require("./routes/attendance.routes");
const configRoutes = require("./routes/config.routes");
const adminRoutes = require("./routes/admin.routes");
const dashboardRoutes = require("./routes/dashboard.routes");
const logRoutes = require("./routes/log.routes");

const app = express();

app.use(helmet());
app.use(limiter);

app.use(cors({ origin: true }));
app.use(express.json({ limit: "10mb" }));

app.use("/uploads", verifyToken, express.static(path.join(__dirname, "uploads")));

app.get("/status/ping", (req, res) => res.json({ status: "ok" }));

app.use("/api/auth", authRoutes);
app.use("/api/screenshots", verifyToken, screenshotRoutes);
app.use("/api/attendance", verifyToken, attendanceRoutes);
app.use("/api/config", verifyToken, configRoutes);
app.use("/api/admin", verifyToken, adminRoutes);
app.use("/api/dashboard", verifyToken, dashboardRoutes);
app.use("/api/logs", verifyToken, logRoutes);

const PORT = process.env.PORT || 8000;
app.listen(PORT, () => {
    console.log(`🚀 ETS Server running on http://localhost:${PORT}`);
});
