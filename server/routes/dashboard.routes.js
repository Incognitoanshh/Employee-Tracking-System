const express             = require("express");
const router              = express.Router();
const dashboardController = require("../controllers/dashboard.controller");
const { requireAdmin }    = require("../middleware/admin.middleware");

// Employee dashboard
router.get("/stats", dashboardController.getStats);

// Admin only routes
router.get("/summary",         requireAdmin, dashboardController.getAdminSummary);
router.get("/recent-activity", requireAdmin, dashboardController.getRecentActivity);
router.get("/charts",          requireAdmin, dashboardController.getChartsData);

module.exports = router;