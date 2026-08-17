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
// Employee ka apna summary — adminOnly NAHI (har employee apna dekh sakta hai;
// employee_id JWT se aata hai, client se nahi).
router.get("/me", dashboardController.getMySummary);

// Today, for everybody — so adminOnly, for exactly the reason written above
// the summary route. This is company-wide: who is absent, who is on leave,
// who came in late. Without the guard any signed-in employee could read it
// straight off the URL.
router.get("/today", adminOnly, dashboardController.getTodayBoard);

module.exports = router;
