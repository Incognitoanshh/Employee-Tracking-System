const express = require("express");

const router = express.Router();

const dashboardController = require(
    "../controllers/dashboard.controller"
);
const { adminOnly } = require("../middleware/admin.middleware");

router.get(
    "/stats",
    adminOnly,
    dashboardController.getStats
);

// BUG FIX: summary/recent-activity/charts company-wide data dete hain
// (sab employees ka data milake) — pehle koi role check nahi tha, koi
// bhi logged-in employee URL seedha hit karke sabka data dekh sakta tha.
// Admin panel hi inhe use karta hai, isliye adminOnly add kiya.
router.get(
    "/summary",
    adminOnly,
    dashboardController.getAdminSummary
);

router.get(
    "/recent-activity",
    adminOnly,
    dashboardController.getRecentActivity
);

router.get(
    "/charts",
    adminOnly,
    dashboardController.getChartsData
);
module.exports = router;
