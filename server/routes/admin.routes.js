const express        = require("express");
const router         = express.Router();
const { adminOnly, superAdminOnly } = require("../middleware/admin.middleware");
const adminCtrl      = require("../controllers/admin.controller");
const reportCtrl     = require("../controllers/report.controller");

// Sab routes admin-only hain
router.use(adminOnly);

// Employees
router.get("/employees",  adminCtrl.getEmployees);
router.post("/employees", adminCtrl.createEmployee);
router.delete("/employees/:employee_id", adminCtrl.deleteEmployee);

// Role change — sirf super_admin
router.post("/employees/:employee_id/role", superAdminOnly, adminCtrl.changeRole);
router.post("/employees/:employee_id/password", adminCtrl.resetPassword);

// Config
router.get("/config/:employee_id", adminCtrl.getConfig);   // GET config for one employee or "global"
router.post("/config",             adminCtrl.saveConfig);   // Save/update config
router.post("/config/shift",       adminCtrl.saveShift);    // Lightweight shift-only save (no full config payload needed)

// Reports
router.get("/reports/attendance", reportCtrl.getAttendanceReport);

// Holidays — company-wide, so any admin may manage them
router.get("/holidays",                  adminCtrl.getHolidays);
router.post("/holidays",                 adminCtrl.addHoliday);
router.delete("/holidays/:holiday_date", adminCtrl.deleteHoliday);
router.post("/force-logout",       adminCtrl.forceLogout);  // Force logout employee
router.post("/toggle-verbose-logging", adminCtrl.toggleVerboseLogging);  // Quick per-employee verbose toggle

// Employee details (modal data)
router.get("/employee/:employee_id", adminCtrl.getEmployeeDetails);

// Screenshots + Logs (with filters)
router.get("/screenshots",         adminCtrl.getScreenshots);
router.get("/logs",                adminCtrl.getLogs);

module.exports = router;
