const express          = require("express");
const router           = express.Router();
const configController = require("../controllers/config.controller");

// verifyToken server.js mein already lagaya hai
// Dobara nahi lagana
router.post("/sync", configController.syncConfig);

module.exports = router;