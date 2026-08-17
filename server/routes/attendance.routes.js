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

// Correcting a shift by hand. Guarded inside the controller rather than by a
// middleware here, because the rule is not "is an admin" alone — it is
// whether this admin may act on THAT employee.
router.patch(
    "/:id/checkout",
    attendanceController.setCheckout
);

module.exports = router;