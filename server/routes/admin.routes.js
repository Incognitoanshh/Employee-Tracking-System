const express        = require("express");
const router         = express.Router();
const { adminOnly, superAdminOnly } = require("../middleware/admin.middleware");
const adminCtrl      = require("../controllers/admin.controller");
const reportCtrl     = require("../controllers/report.controller");
const teamCtrl       = require("../controllers/team.controller");
const alertsCtrl     = require("../controllers/alerts.controller");

// Sab routes admin-only hain
router.use(adminOnly);

// Employees
router.get("/employees",  adminCtrl.getEmployees);
router.get("/employees/next-id", adminCtrl.nextEmployeeId);
router.post("/employees", adminCtrl.createEmployee);
router.delete("/employees/:employee_id", adminCtrl.deleteEmployee);

// Role change — sirf super_admin
router.post("/employees/:employee_id/role", superAdminOnly, adminCtrl.changeRole);
router.post("/employees/:employee_id/password", adminCtrl.resetPassword);
// The name somebody is shown by. Without this there was no way to correct
// one after the account was made.
router.post("/employees/:employee_id/profile", adminCtrl.updateProfile);
// Suspend / restore. Role rules are enforced in the controller, not here:
// an admin may suspend employees, a super admin may suspend admins too.
router.post("/employees/:employee_id/suspend", adminCtrl.setSuspended);

// Alerts — what is wrong right now. Computed on read; nothing is stored.
// Settings before /alerts so neither route can shadow the other.
router.get("/alerts/settings",  alertsCtrl.getSettings);
router.post("/alerts/settings", alertsCtrl.saveSettings);
router.get("/alerts",           alertsCtrl.getAlerts);

// Config
router.get("/config/:employee_id", adminCtrl.getConfig);   // GET config for one employee or "global"
router.post("/config",             adminCtrl.saveConfig);   // Save/update config
router.post("/config/shift",       adminCtrl.saveShift);    // Lightweight shift-only save (no full config payload needed)

// Screenshots — delete specific captures. There is deliberately no
// equivalent for activity logs: an audit trail an admin can edit is not one.
router.post("/screenshots/delete", adminCtrl.deleteScreenshots);

// What the coming days look like for one employee — the only way to check a
// weekly off without waiting for the weekend it applies to.
router.get("/upcoming/:employee_id", adminCtrl.getUpcomingDays);

// Data retention — company-wide. Super admin only: this is the one setting
// whose effect is deleting things, and it should not be a shared control.
router.get("/retention",  superAdminOnly, adminCtrl.getRetention);
router.post("/retention", superAdminOnly, adminCtrl.saveRetention);

// Reports
router.get("/reports/attendance", reportCtrl.getAttendanceReport);
// Administrative actions over a period — who reset what, who deleted what.
// Super admin only: it is a report ABOUT what admins do.
router.get("/reports/audit", superAdminOnly, reportCtrl.getAuditReport);

// Holidays — company-wide, so any admin may manage them
router.get("/holidays",                  adminCtrl.getHolidays);
router.post("/holidays",                 adminCtrl.addHoliday);
router.delete("/holidays/:holiday_date", adminCtrl.deleteHoliday);
router.post("/force-logout",       adminCtrl.forceLogout);  // Force logout employee
router.post("/toggle-verbose-logging", adminCtrl.toggleVerboseLogging);  // Quick per-employee verbose toggle

// ── Teams and channels ─────────────────────────────────────────────────────
// Any admin may manage any team. One person being away should not stop the
// rest of the work, and a team whose only administrator has left would
// otherwise need a super admin to unstick it.
router.get("/teams",           teamCtrl.listTeams);
router.post("/teams",          teamCtrl.createTeam);
router.get("/teams/:id",       teamCtrl.getTeam);
router.patch("/teams/:id",     teamCtrl.updateTeam);
// Archive, not delete: deleting a team would take every conversation in it.
router.post("/teams/:id/archive", teamCtrl.archiveTeam);

router.post("/teams/:id/channels", teamCtrl.createChannel);
router.patch("/channels/:id",      teamCtrl.updateChannel);
router.post("/channels/:id/announce", teamCtrl.postAnnouncement);

router.post("/teams/:id/members",   teamCtrl.addMembers);
router.delete("/teams/:id/members/:employee_id", teamCtrl.removeMember);
router.post("/channels/:id/members", teamCtrl.addChannelMembers);
router.delete("/channels/:id/members/:employee_id", teamCtrl.removeChannelMember);

// Reading somebody's conversation. Super admin only, a purpose is required,
// and every read is recorded in chat_access_log — see team.controller.
router.post("/chat/view",      superAdminOnly, teamCtrl.viewChannel);
router.get("/chat/access-log", superAdminOnly, teamCtrl.getAccessLog);

// Employee details (modal data)
router.get("/employee/:employee_id", adminCtrl.getEmployeeDetails);

// Screenshots + Logs (with filters)
router.get("/screenshots",         adminCtrl.getScreenshots);
router.get("/logs",                adminCtrl.getLogs);

module.exports = router;
