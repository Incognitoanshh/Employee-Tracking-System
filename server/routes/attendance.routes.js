const express = require("express");

const router = express.Router();

const attendanceController = require(
    "../controllers/attendance.controller"
);

router.get(
    "/all",
    attendanceController.getAttendance
);
router.post(
    "/login",
    attendanceController.loginAttendance
);

router.post(
    "/logout",
    attendanceController.logoutAttendance
);

module.exports = router;