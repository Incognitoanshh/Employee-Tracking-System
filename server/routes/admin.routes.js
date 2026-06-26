const express        = require("express");
const router         = express.Router();
const adminController = require("../controllers/admin.controller");
const { requireAdmin } = require("../middleware/admin.middleware");

// Saare admin routes pe requireAdmin lagao
router.get(    "/employees",                requireAdmin, adminController.getEmployees);
router.post(   "/employees",                requireAdmin, adminController.createEmployee);
router.delete( "/employees/:employee_id",   requireAdmin, adminController.deleteEmployee);

router.get(    "/config/:employee_id",      requireAdmin, adminController.getConfig);
router.post(   "/config",                   requireAdmin, adminController.saveConfig);

router.post(   "/force-logout",             requireAdmin, adminController.forceLogout);
router.post(   "/toggle-verbose-logging",   requireAdmin, adminController.toggleVerboseLogging);

router.get(    "/screenshots",              requireAdmin, adminController.getScreenshots);
router.get(    "/logs",                     requireAdmin, adminController.getLogs);
router.get(    "/employee/:employee_id",    requireAdmin, adminController.getEmployeeDetails);

module.exports = router;