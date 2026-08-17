/**
 * An employee's own payroll. Two routes, and neither takes an employee id —
 * except the payslip, where it is optional, only an admin may use it, and it
 * is checked in the controller.
 */
const express = require("express");
const router = express.Router();
const payrollCtrl = require("../controllers/payroll.controller");

router.get("/mine", payrollCtrl.mine);
router.get("/payslip/:month", payrollCtrl.payslip);

module.exports = router;
