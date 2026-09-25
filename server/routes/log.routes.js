const express = require("express");

const router = express.Router();

const logController = require(
    "../controllers/log.controller"
);

router.post(
    "/create",
    logController.createLog
);
router.get(
    "/all",
    logController.getLogs
);
router.post(
    "/idle-daily",
    logController.recordIdleDaily
);
// What kind of minutes those idle seconds were made of — see
// utils/alert_rules.js, which is the only thing that reads them.
router.post(
    "/activity-minutes",
    logController.recordActivityMinutes
);

module.exports = router;