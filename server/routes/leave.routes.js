/**
 * An employee's own leave. Nothing here takes an employee id — the only
 * requests these routes can reach are the caller's, which is the same rule
 * profile.routes follows and the reason there is nothing to tamper with.
 *
 * The administrator's half lives under /api/admin/leave, behind adminOnly.
 */
const express = require("express");
const router = express.Router();
const leaveCtrl = require("../controllers/leave.controller");

router.post("/", leaveCtrl.apply);
router.get("/mine", leaveCtrl.mine);
router.post("/:id/cancel", leaveCtrl.cancel);

module.exports = router;
