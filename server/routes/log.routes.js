const express = require("express");

const router = express.Router();

const logController = require(
    "../controllers/log.controller"
);
const { getAuditLogs } = require("../controllers/logs.controller");

router.get("/audit", authMiddleware, getAuditLogs);
router.post(
    "/create",
    logController.createLog
);
router.get(
    "/all",
    logController.getLogs
);

// ✅ Upload idle log - POST /logs/upload
router.post(
    "/upload",
    logController.uploadIdleLog
);

module.exports = router;