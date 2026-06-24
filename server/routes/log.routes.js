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

module.exports = router;