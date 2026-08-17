/**
 * The two status columns, checked as arithmetic anybody can do by hand.
 *
 * No database and no server here: attendanceState and shiftState take plain
 * values and return plain values, which is why they can be pinned down this
 * precisely. The cases that matter are the ones where the old single column
 * gave an answer that was wrong, or worse, contradicted the cell beside it.
 *
 * Run:  node server/tests/test_attendance_state.js
 */
const { attendanceState, shiftState } = require("../utils/attendance_state");

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}

const SHIFT = { shiftStart: "09:00", shiftEnd: "18:00", graceMinutes: 10 };
const at = (h, m = 0) => h * 60 + m;

console.log("\nWhat happened to the record");
check("open with a live heartbeat is Active",
    attendanceState({ logout_time: null, session_live: true }).label === "Active");
check("open with nobody there is Incomplete, not Active",
    attendanceState({ logout_time: null, session_live: false }).label === "Incomplete",
    "this is the row that read ACTIVE for sixteen hours after the app closed");
check("closed is Completed",
    attendanceState({ logout_time: "2026-08-17 10:00:00", session_live: false })
        .label === "Completed");
check("a closed row is Completed even if the person is signed in elsewhere",
    attendanceState({ logout_time: "2026-08-17 10:00:00", session_live: true })
        .label === "Completed",
    "a live session is about the person; this column is about the record");

console.log("\nHow the shift went");
check("arriving at nine is On Time",
    shiftState({ ...SHIFT, loginMinutes: at(9) }).label === "On Time");
check("arriving inside the grace period is still On Time",
    shiftState({ ...SHIFT, loginMinutes: at(9, 8) }).label === "On Time");
check("arriving at 09:40 is Late 40m",
    shiftState({ ...SHIFT, loginMinutes: at(9, 40) }).label === "Late 40m");
check("leaving an hour early says so",
    shiftState({ ...SHIFT, loginMinutes: at(9), logoutMinutes: at(17) })
        .label === "Early Exit 1h");
check("staying two hours late says so",
    shiftState({ ...SHIFT, loginMinutes: at(9), logoutMinutes: at(20) })
        .label === "Overtime 2h");
check("a few minutes either side of the end is neither",
    shiftState({ ...SHIFT, loginMinutes: at(9), logoutMinutes: at(18, 5) })
        .label === "On Time",
    "grace has to apply at both ends or every day reads as overtime");

console.log("\nLate AND early — one headline, the rest kept");
const both = shiftState({ ...SHIFT, loginMinutes: at(10), logoutMinutes: at(16) });
check("lateness is the headline", both.label === "Late 1h", both.label);
check("and leaving early is not thrown away",
    (both.notes || []).some((n) => n.label.startsWith("Early Exit")),
    JSON.stringify(both.notes));

console.log("\nThe cases the old column got wrong");
check("a second shift the same day is not judged late",
    shiftState({ ...SHIFT, loginMinutes: at(14), isFirstOfDay: false })
        .label === "Extra Session",
    "you cannot arrive twice");
check("and the word does not share a verb with 'Not signed out'",
    !/sign/i.test(shiftState({ ...SHIFT, loginMinutes: at(14), isFirstOfDay: false })
        .label),
    "'Signed in again' beside 'Not signed out' was read as one contradiction");
check("half-day leave outranks lateness",
    shiftState({ ...SHIFT, loginMinutes: at(14), leave: { half: true } })
        .label === "Half Day",
    "an approved arrangement must not become a black mark");
check("full leave says On Leave",
    shiftState({ ...SHIFT, loginMinutes: at(14), leave: { half: false } })
        .label === "On Leave");
check("a holiday is a Day Off, never Late",
    shiftState({ ...SHIFT, loginMinutes: at(11), isDayOff: true }).label === "Day Off",
    "calling this late is what costs somebody a day's pay");
check("no shift configured says so rather than guessing 09:00",
    shiftState({ loginMinutes: at(23), shiftStart: null, shiftEnd: null })
        .label === "No Shift Set");
check("signing in after the shift ended is not lateness",
    shiftState({ ...SHIFT, loginMinutes: at(23) }).label === "Outside Shift",
    "'Late 14h' is worse than useless");

console.log("\nThe night shift, where this kind of arithmetic usually breaks");
const NIGHT = { shiftStart: "22:00", shiftEnd: "06:00", graceMinutes: 10 };
check("starting at ten at night is On Time",
    shiftState({ ...NIGHT, loginMinutes: at(22) }).label === "On Time");
check("leaving at six in the morning is neither early nor overtime",
    shiftState({ ...NIGHT, loginMinutes: at(22), logoutMinutes: at(6) })
        .label === "On Time",
    "the end is on the NEXT day — this read as 16 hours early before");
check("leaving at four in the morning is two hours early",
    shiftState({ ...NIGHT, loginMinutes: at(22), logoutMinutes: at(4) })
        .label === "Early Exit 2h");
check("staying until eight is two hours over",
    shiftState({ ...NIGHT, loginMinutes: at(22), logoutMinutes: at(8) })
        .label === "Overtime 2h");

console.log("\nNothing here is ever called 'Reconnected' again");
const everyLabel = [
    shiftState({ ...SHIFT, loginMinutes: at(9) }),
    shiftState({ ...SHIFT, loginMinutes: at(14), isFirstOfDay: false }),
    shiftState({ ...SHIFT, loginMinutes: at(9), logoutMinutes: at(17) }),
    shiftState({ ...SHIFT, loginMinutes: at(11), isDayOff: true }),
].map((r) => r.label);
check("no label mentions reconnecting or signing in",
    !everyLabel.some((l) => /reconnect|signed in/i.test(l)),
    everyLabel.join(" | "));

console.log(failures === 0
    ? "\nall attendance state checks passed"
    : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
