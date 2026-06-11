const express        = require("express");
const router         = express.Router();
const { adminOnly }  = require("../middleware/admin.middleware");
const adminCtrl      = require("../controllers/admin.controller");

// Sab routes admin-only hain
router.use(adminOnly);

// Employees
router.get("/employees",          adminCtrl.getEmployees);

// Config
router.get("/config/:employee_id", adminCtrl.getConfig);   // GET config for one employee or "global"
router.post("/config",             adminCtrl.saveConfig);   // Save/update config
router.post("/force-logout",       adminCtrl.forceLogout);  // Force logout employee

// Screenshots + Logs (with filters)
router.get("/screenshots",         adminCtrl.getScreenshots);
router.get("/logs",                adminCtrl.getLogs);

module.exports = router;
