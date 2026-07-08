const express        = require("express");
const router         = express.Router();
const { adminOnly }  = require("../middleware/admin.middleware");
const adminCtrl      = require("../controllers/admin.controller");

// Sab routes admin-only hain
router.use(adminOnly);

// Employees
router.get("/employees",  adminCtrl.getEmployees);
router.post("/employees", adminCtrl.createEmployee);
router.delete("/employees/:employee_id", adminCtrl.deleteEmployee);

// Config
router.get("/config/:employee_id", adminCtrl.getConfig);   // GET config for one employee or "global"
router.post("/config",             adminCtrl.saveConfig);   // Save/update config
router.post("/config/shift",       adminCtrl.saveShift);    // Lightweight shift-only save (no full config payload needed)
router.post("/force-logout",       adminCtrl.forceLogout);  // Force logout employee
router.post("/toggle-verbose-logging", adminCtrl.toggleVerboseLogging);  // Quick per-employee verbose toggle

// Employee details (modal data)
router.get("/employee/:employee_id", adminCtrl.getEmployeeDetails);

// Screenshots + Logs (with filters)
router.get("/screenshots",         adminCtrl.getScreenshots);
router.get("/logs",                adminCtrl.getLogs);

module.exports = router;
