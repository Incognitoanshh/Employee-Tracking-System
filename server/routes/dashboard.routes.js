const express = require("express");

const router = express.Router();

const dashboardController = require(
    "../controllers/dashboard.controller"
);

router.get(
    "/stats",
    dashboardController.getStats
);

// Admin dashboard compatible endpoints (summary + recent activity)
router.get(
    "/summary",
    dashboardController.getAdminSummary
);

router.get(
    "/recent-activity",
    dashboardController.getRecentActivity
);

router.get(
    "/charts",
    dashboardController.getChartsData
);
module.exports = router;
