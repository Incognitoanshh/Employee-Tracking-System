/**
 * TWO QUESTIONS, TWO ANSWERS — which is the whole point of this file.
 *
 * The attendance list used to have one "Status" column, and it answered two
 * unrelated questions at once. "On time" is about the shift. "Not signed out"
 * is about the record. "Reconnected" was about neither — it existed only to
 * fill a cell that had nothing to say. An administrator reading two of them
 * side by side read a contradiction, and reported it as one.
 *
 * So:
 *
 *   ATTENDANCE STATUS — what happened to this record.
 *       Active       open, and the person is there now
 *       Incomplete   open, and nobody is there — never signed out
 *       Completed    closed, with an end time
 *
 *   SHIFT STATUS — how it compares to the shift they were meant to work.
 *       On Time · Late · Early Exit · Overtime · Outside Shift
 *       Half Day · On Leave · Day Off · No Shift Set · Extra Session
 *
 * NOTHING IS STORED. Both are derived on read, from the shift the employee has
 * now — the same choice the old status made, for the same reason: a shift
 * corrected today fixes the history that was misread under the old one.
 *
 * The day-level statuses in the specification — Leave, Holiday, Weekly Off,
 * Absent — are NOT here. They describe a day on which there is no attendance
 * record at all, so they cannot be a property of a record. The reports build
 * those, from the same holiday and weekly-off rules this file uses.
 */

const { classifyLogin, formatGap } = require("./attendance_status");

/**
 * What happened to this record?
 *
 * @param {object} row
 * @param {*} row.logout_time    null while the shift is open
 * @param {boolean} row.session_live  is there a live heartbeat right now
 */
function attendanceState(row) {
    if (row.logout_time) {
        return { status: "completed", label: "Completed" };
    }
    if (row.session_live) {
        return { status: "active", label: "Active" };
    }
    // OPEN, BUT NOBODY IS THERE. This is the row the old page called ACTIVE
    // for up to sixteen hours after the app had been closed, while the
    // employee list called the same person offline at the same moment.
    return { status: "incomplete", label: "Incomplete" };
}

/**
 * How does this shift compare to the one they were meant to work?
 *
 * @param {object} input
 * @param {number}  input.loginMinutes   IST minutes since midnight
 * @param {?number} input.logoutMinutes  same, or null while still open
 * @param {?string} input.shiftStart     "HH:MM", or null if none is set
 * @param {?string} input.shiftEnd       "HH:MM"
 * @param {number}  input.graceMinutes
 * @param {boolean} input.isDayOff       weekly off or holiday
 * @param {?object} input.leave          { half: boolean } if approved leave
 * @param {boolean} input.isFirstOfDay   false for a second shift the same day
 */
function shiftState({
    loginMinutes,
    logoutMinutes = null,
    shiftStart,
    shiftEnd,
    graceMinutes = 10,
    isDayOff = false,
    leave = null,
    isFirstOfDay = true,
}) {
    // LEAVE OUTRANKS EVERYTHING BELOW IT. Somebody with approved half-day
    // leave who signs in at two is not late — that was the arrangement, and
    // calling it lateness is how an approval turns into a black mark.
    if (leave) {
        return leave.half
            ? { status: "half_day", label: "Half Day" }
            : { status: "on_leave", label: "On Leave" };
    }

    // A SECOND SHIFT IN A DAY IS NOT AN ARRIVAL. You cannot arrive twice, so
    // it is not judged late or on time. It is now rare — a live shift is
    // resumed rather than replaced — so this means a real gap: they left and
    // came back.
    if (!isFirstOfDay) {
        return { status: "extra", label: "Extra Session" };
    }

    const arrival = classifyLogin({
        loginMinutes, shiftStart, shiftEnd, graceMinutes, isDayOff,
    });

    // No shift, a day off, or a sign-in after the shift had already finished:
    // there is no start or end to measure an exit against either.
    //
    // The labels are re-cased here rather than in attendance_status, which
    // the reports also read — one column's capitalisation is not worth
    // changing a shared module for.
    const RELABEL = {
        day_off: "Day Off",
        unknown: "No Shift Set",
        outside_shift: "Outside Shift",
    };
    if (arrival.status !== "on_time" && arrival.status !== "late") {
        return {
            status: arrival.status,
            label: RELABEL[arrival.status] || arrival.label,
            late_minutes: null,
        };
    }

    // LATE IS REPORTED IN PREFERENCE TO ANYTHING ELSE, because it is the one
    // an administrator acts on, and because a late arrival is a fact about
    // the whole shift rather than about its end. Leaving early on top of it
    // is carried in `notes` rather than replacing the headline.
    const notes = [];
    const end = toMinutesSafe(shiftEnd);
    if (logoutMinutes !== null && end !== null) {
        const start = toMinutesSafe(shiftStart);
        // An overnight shift ends on the next calendar day, so an exit at
        // 02:00 against a 22:00–06:00 shift is four hours BEFORE the end,
        // not twenty after it.
        //
        // THE TEST HERE IS "BEFORE THE START", NOT "BEFORE THE END". Written
        // the other way, leaving at exactly 06:00 — the ordinary end of the
        // shift, the most common exit there is — fell outside the condition
        // and came out as Early Exit 24h. Caught by the night-shift checks,
        // which is the only reason they exist.
        const overnight = start !== null && end <= start;
        const endOnLine = overnight ? end + 24 * 60 : end;
        const outOnLine = (overnight && logoutMinutes < start)
            ? logoutMinutes + 24 * 60
            : logoutMinutes;
        const overrun = outOnLine - endOnLine;
        if (overrun < -graceMinutes) {
            notes.push({ status: "early_exit",
                         label: `Early Exit ${formatGap(-overrun)}` });
        } else if (overrun > graceMinutes) {
            notes.push({ status: "overtime",
                         label: `Overtime ${formatGap(overrun)}` });
        }
    }

    if (arrival.status === "late") {
        return { status: "late", label: arrival.label,
                 late_minutes: arrival.late_minutes, notes };
    }
    // On time, but the exit is worth saying — it becomes the headline,
    // because "On Time" beside an hour of unpaid overtime tells nobody
    // anything they needed to know.
    if (notes.length > 0) {
        return { ...notes[0], late_minutes: null, notes: [] };
    }
    return { status: "on_time", label: "On Time", late_minutes: null, notes };
}

/** "09:00" -> 540, anything unparseable -> null. */
function toMinutesSafe(hhmm) {
    const match = /^(\d{1,2}):(\d{2})/.exec(String(hhmm || "").trim());
    if (!match) return null;
    const hours = Number(match[1]);
    const mins = Number(match[2]);
    if (hours > 23 || mins > 59) return null;
    return hours * 60 + mins;
}

module.exports = { attendanceState, shiftState };
