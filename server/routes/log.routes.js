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

module.exports = router;