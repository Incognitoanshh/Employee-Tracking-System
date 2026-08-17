/**
 * Today's numbers, and the one property that makes them checkable.
 *
 * EVERY EMPLOYEE IS IN EXACTLY ONE BUCKET, so Day Off + On Leave + Present +
 * Absent must equal the headcount. Every case below asserts that as well as
 * the individual figure — a card that is wrong on its own is hard to spot,
 * but a set that no longer adds up to the headcount is obvious, and this is
 * the check that makes it obvious in a test rather than in production.
 *
 * Run:  node server/tests/test_today_board.js
 */
const { todayBoard } = require("../utils/today_board");

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}

const TODAY = "2026-08-17";        // a Monday
const SUNDAY = "2026-08-16";

const CONFIG = {
    shift_start: "09:00", shift_end: "18:00",
    weekly_offs: "7", late_grace_minutes: 10,
};

function board(overrides = {}) {
    const {
        people = ["E1", "E2", "E3", "E4"],
        shifts = new Map(),
        leave = new Map(),
        holidays = new Set(),
        today = TODAY,
        configs = null,
    } = overrides;
    return todayBoard({
        employees: people.map((employee_id) => ({ employee_id, role: "employee" })),
        configs: configs || new Map([[null, CONFIG]]),
        holidays,
        leaveToday: leave,
        shifts,
        today,
    });
}

const addsUp = (b) =>
    b.day_off + b.on_leave + b.present + b.absent === b.headcount;

console.log("\nNobody has done anything yet today");
let b = board();
check("everyone is absent", b.absent === 4, JSON.stringify(b));
check("and nobody is present", b.present === 0);
check("the buckets add up to the headcount", addsUp(b), JSON.stringify(b));

console.log("\nOne at work, one who has gone home, one on leave, one absent");
b = board({
    shifts: new Map([
        ["E1", { open: true, live: true, first_login_minutes: 9 * 60 }],
        ["E2", { open: false, live: false, first_login_minutes: 9 * 60 }],
    ]),
    leave: new Map([["E3", { half: false }]]),
});
check("one is active", b.active === 1, JSON.stringify(b));
check("one has worked and left", b.worked === 1);
check("present counts both of them", b.present === 2);
check("one is on leave", b.on_leave === 1);
check("one is absent", b.absent === 1);
check("nobody is late", b.late === 0);
check("and it still adds up", addsUp(b), JSON.stringify(b));

console.log("\nLate is counted as well as present, never instead of it");
b = board({
    shifts: new Map([["E1", { open: true, live: true, first_login_minutes: 10 * 60 }]]),
});
check("the late arrival is present", b.present === 1, JSON.stringify(b));
check("and counted late", b.late === 1);
check("the totals are unaffected by lateness", addsUp(b), JSON.stringify(b));
check("arriving inside the grace period is not late",
    board({ shifts: new Map([["E1",
        { open: true, live: true, first_login_minutes: 9 * 60 + 8 }]]) }).late === 0);

console.log("\nA weekly off is not an absence");
b = board({ today: SUNDAY });
check("everybody is on a day off", b.day_off === 4, JSON.stringify(b));
check("and NOBODY is absent", b.absent === 0,
    "marking a whole company absent on a Sunday is the bug this prevents");
check("it adds up", addsUp(b), JSON.stringify(b));

console.log("\nA holiday is not an absence either");
b = board({ holidays: new Set([TODAY]) });
check("everybody is on a day off", b.day_off === 4, JSON.stringify(b));
check("and nobody is absent", b.absent === 0);

console.log("\nWorking on a day off still counts as working");
b = board({
    today: SUNDAY,
    shifts: new Map([["E1", { open: true, live: true, first_login_minutes: 10 * 60 }]]),
});
check("the day off outranks it in the count", b.day_off === 4, JSON.stringify(b));
check("and they are not marked late on a day nobody was expected",
    b.late === 0,
    "calling somebody late on their day off is what costs them pay");

console.log("\nAn open shift nobody is sitting at");
b = board({
    shifts: new Map([["E1", { open: true, live: false, first_login_minutes: 9 * 60 }]]),
});
check("is not counted as active", b.active === 0, JSON.stringify(b));
check("but the person did come to work, so they are present", b.present === 1);
check("and the row is flagged as needing tidying up", b.not_signed_out === 1);
check("it adds up", addsUp(b), JSON.stringify(b));

console.log("\nPer-employee settings beat the global ones");
b = board({
    people: ["E1", "E2"],
    configs: new Map([
        [null, CONFIG],
        // A night worker: 22:00–06:00, Sunday off. Arriving at 22:00 is on
        // time for them and would be thirteen hours late against the global
        // shift, which is what makes this worth its own case.
        ["E2", { shift_start: "22:00", shift_end: "06:00",
                 weekly_offs: "7", late_grace_minutes: 10 }],
    ]),
    shifts: new Map([
        ["E1", { open: true, live: true, first_login_minutes: 9 * 60 }],
        ["E2", { open: true, live: true, first_login_minutes: 22 * 60 }],
    ]),
});
check("the night worker is on time, not thirteen hours late",
    b.late === 0, JSON.stringify(b));
check("both are present", b.present === 2);

console.log("\nAn empty company");
b = board({ people: [] });
check("counts nothing and divides by nothing",
    b.headcount === 0 && b.absent === 0 && addsUp(b), JSON.stringify(b));

console.log(failures === 0
    ? "\nall today board checks passed"
    : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
