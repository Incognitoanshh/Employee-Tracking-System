const express             = require("express");
const router              = express.Router();
const attendanceController = require("../controllers/attendance.controller");

// verifyToken server.js mein already hai
router.get( "/all",    attendanceController.getAttendance);
router.post("/login",  attendanceController.loginAttendance);
router.post("/logout", attendanceController.logoutAttendance);

module.exports = router;