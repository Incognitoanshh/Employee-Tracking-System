/**
 * Where everybody stands today — one pass, one set of rules.
 *
 * The dashboard used to say Online and Offline and nothing else, which is a
 * fact about network sessions rather than about attendance. "Who is absent
 * today" could not be answered from any screen in the product; it had to be
 * worked out by eye from the attendance list.
 *
 * EVERY EMPLOYEE LANDS IN EXACTLY ONE BUCKET. That is the property worth
 * having: the numbers add up to the headcount, so a card that looks wrong can
 * be checked by adding the others. Ordered by what outranks what:
 *
 *   Day Off    a weekly off or a holiday — nobody was expected
 *   On Leave   approved leave for today
 *   Active     a shift open now, with a live session
 *   Worked     a shift today that has since been closed
 *   Absent     none of the above, on a day they were expected
 *
 * Late is counted ALONGSIDE those, not instead of them: somebody who arrived
 * late is present and late, and putting them in a bucket of their own would
 * make Present understate the people at work.
 *
 * ABSENT IS DERIVED, NOT STORED. There is no nightly job marking people
 * absent, deliberately: a stored absence is wrong the moment leave is
 * approved after the fact, and then somebody has to remember to go back and
 * fix it. Derived from the same three sources every time, it corrects itself.
 */

const { isNonWorkingDay, classifyLogin, toMinutes } = require("./attendance_status");

/**
 * @param {object} deps
 * @param {Array}  deps.employees   { employee_id, role }
 * @param {Map}    deps.configs     employee_id -> config row (null key = global)
 * @param {Set}    deps.holidays    'YYYY-MM-DD'
 * @param {Map}    deps.leaveToday  employee_id -> { half }
 * @param {Map}    deps.shifts      employee_id -> { open, live, first_login_minutes }
 * @param {string} deps.today       'YYYY-MM-DD' in IST
 */
function todayBoard({ employees, configs, holidays, leaveToday, shifts, today }) {
    const global = configs.get(null) || {};
    const board = {
        headcount: 0,
        present: 0,
        active: 0,
        worked: 0,
        on_leave: 0,
        absent: 0,
        day_off: 0,
        late: 0,
        not_signed_out: 0,
    };

    for (const employee of employees) {
        board.headcount += 1;
        const config = configs.get(employee.employee_id) || global;
        const shift = shifts.get(employee.employee_id) || null;

        if (isNonWorkingDay(today, config.weekly_offs ?? global.weekly_offs, holidays)) {
            board.day_off += 1;
            continue;
        }
        if (leaveToday.has(employee.employee_id)) {
            board.on_leave += 1;
            continue;
        }
        if (!shift) {
            board.absent += 1;
            continue;
        }

        if (shift.open && shift.live) {
            board.active += 1;
        } else {
            board.worked += 1;
            // AN OPEN SHIFT WITH NOBODY IN IT is still somebody who came to
            // work — they are counted present, and counted again here so the
            // rows needing to be tidied up have a number of their own.
            if (shift.open) board.not_signed_out += 1;
        }
        board.present += 1;

        const start = config.shift_start ?? global.shift_start;
        const end = config.shift_end ?? global.shift_end;
        if (shift.first_login_minutes !== null && start && end) {
            const verdict = classifyLogin({
                loginMinutes: shift.first_login_minutes,
                shiftStart: String(start),
                shiftEnd: String(end),
                graceMinutes: config.late_grace_minutes
                    ?? global.late_grace_minutes ?? 10,
                isDayOff: false,
            });
            if (verdict.status === "late") board.late += 1;
        }
    }

    return board;
}

module.exports = { todayBoard, toMinutes };
