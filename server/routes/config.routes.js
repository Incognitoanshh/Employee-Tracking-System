
const express    = require("express");
const router     = express.Router();
const configController = require("../controllers/config.controller");

router.post(
    "/sync",
    configController.syncConfig
);

module.exports = router;
