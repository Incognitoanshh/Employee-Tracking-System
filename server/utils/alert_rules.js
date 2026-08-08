/**
 * When is something worth telling an administrator about?
 *
 * Pure decisions, no database. Facts are gathered by the controller and
 * judged here, so every rule can be tested against a made-up Tuesday
 * afternoon instead of by waiting for a real one.
 *
 * WHY ALERTS ARE COMPUTED, NOT STORED
 * There is no alerts table and no background job. The panel asks "what is
 * wrong right now" and this answers from the data as it stands. Storing them
 * would mean a scheduler, a dedup rule, a purge, and — the real problem — a
 * row that goes on claiming somebody never logged in after they have logged
 * in. An alert that outlives its cause teaches people to ignore alerts.
 *
 * For the same reason nothing can be dismissed. An alert disappears when it
 * stops being true, and only then. A dismiss button would let a genuine
 * problem be waved away, and the one this feature exists for — an app that
 * quietly stopped reporting — is precisely the one somebody would wave away.
 *
 * WHAT IS DELIBERATELY NOT DECIDED HERE
 * Every threshold arrives as a number from settings. None is written into
 * this file. The owner of this system decides what counts as late and what
 * counts as too much idle; the code decides nothing about shifts on its own.
 *
 * THE NON-WORKING DAY RULE APPLIES TO ALL OF THEM. Firing "did not log in"
 * at somebody on their weekly off, or on a holiday, is how an alert list
 * becomes noise nobody reads — and this product already knows which days
 * those are.
 */

const { toMinutes, isNonWorkingDay } = require("./attendance_status");

/** Severities, in the order the panel should show them. */
const SEVERITY = { HIGH: "HIGH", MEDIUM: "MEDIUM", LOW: "LOW" };

const RANK = { HIGH: 0, MEDIUM: 1, LOW: 2 };

/**
 * Defaults, used only when a setting has never been saved.
 *
 * Chosen to be quiet rather than clever: the first day of alerts should not
 * arrive as forty rows. They are all meant to be changed in Configuration.
 */
const DEFAULTS = {
    alerts_enabled: true,
    // Hours with no sign of life before the app is presumed to have stopped.
    // A whole day, so that one evening of a laptop being shut is not an
    // incident, but a machine that never came back on Monday is.
    alert_silent_hours: 24,
    // Minutes after shift start — AFTER the grace period the shift already
    // defines — before "has not logged in" is worth saying.
    alert_late_login_minutes: 30,
    // Idle minutes in one day before it is worth a look.
    alert_idle_minutes: 180,
};

function setting(settings, key) {
    const raw = settings ? settings[key] : undefined;
    if (raw === undefined || raw === null || raw === "") return DEFAULTS[key];
    if (typeof DEFAULTS[key] === "boolean") {
        return raw === true || raw === "true" || raw === "1" || raw === 1;
    }
    const number = Number(raw);
    return Number.isFinite(number) && number >= 0 ? number : DEFAULTS[key];
}

/**
 * Has this employee's app gone quiet?
 *
 * `lastSeenMinutes` is minutes since the most recent evidence of ANY kind —
 * a heartbeat, a login, a screenshot. Null means there has never been any.
 *
 * THIS IS THE RULE THE WHOLE FEATURE WAS ASKED FOR. Until now a stopped app
 * and an employee on leave looked identical from the admin panel: both simply
 * stopped appearing. Nobody finds out that tracking was off until they go
 * looking for a screenshot that was never taken, which is usually weeks late
 * and always at the worst moment.
 */
function notReporting({ employee, lastSeenMinutes, settings }) {
    const hours = setting(settings, "alert_silent_hours");
    if (lastSeenMinutes === null || lastSeenMinutes === undefined) {
        // Never once reported. An account that was created and never used is
        // worth saying out loud — it usually means the app was never
        // installed on that machine at all.
        return {
            type: "NEVER_REPORTED",
            severity: SEVERITY.MEDIUM,
            employee_id: employee.employee_id,
            employee_name: employee.full_name || employee.username,
            title: "Has never reported",
            detail: "This account has never sent anything. The app may not be installed.",
        };
    }
    if (lastSeenMinutes < hours * 60) return null;
    return {
        type: "NOT_REPORTING",
        severity: SEVERITY.HIGH,
        employee_id: employee.employee_id,
        employee_name: employee.full_name || employee.username,
        title: `No data for ${describeGap(lastSeenMinutes)}`,
        detail: "The app has sent nothing — it may be closed, uninstalled, "
              + "or the machine may be off. Tracking is not running.",
        minutes: lastSeenMinutes,
    };
}

/**
 * Shift started, nobody logged in.
 *
 * Silent unless the shift is actually configured. Guessing a start time for
 * somebody who has none would invent a rule the owner never set, and this is
 * the one area where that is explicitly not the code's decision to make.
 */
function noLoginAfterShiftStart({ employee, nowMinutes, isoDate, holidays, loggedInToday, settings }) {
    if (loggedInToday) return null;
    if (!employee.shift_start) return null;
    if (isNonWorkingDay(isoDate, employee.weekly_offs, holidays)) return null;

    const start = toMinutes(employee.shift_start);
    if (start === null) return null;

    const grace = Number(employee.late_grace_minutes) || 0;
    const extra = setting(settings, "alert_late_login_minutes");
    const due = start + grace + extra;
    if (nowMinutes < due) return null;

    // A night shift crossing midnight would make "minutes past due" enormous
    // and meaningless once the clock has wrapped. Only the part of the day
    // after the shift starts can be late for it.
    const late = nowMinutes - due;

    return {
        type: "NO_LOGIN",
        severity: SEVERITY.MEDIUM,
        employee_id: employee.employee_id,
        employee_name: employee.full_name || employee.username,
        title: `Not logged in — shift started ${describeGap(nowMinutes - start)} ago`,
        detail: `Shift begins at ${String(employee.shift_start).slice(0, 5)}`
              + (grace ? ` with ${grace} min grace.` : ".")
              + " There is no attendance record today.",
        minutes: late,
    };
}

/** More idle time today than the owner is willing to ignore. */
function tooMuchIdle({ employee, idleMinutes, isoDate, holidays, settings }) {
    const limit = setting(settings, "alert_idle_minutes");
    if (!idleMinutes || idleMinutes < limit) return null;
    if (isNonWorkingDay(isoDate, employee.weekly_offs, holidays)) return null;
    return {
        type: "HIGH_IDLE",
        severity: SEVERITY.LOW,
        employee_id: employee.employee_id,
        employee_name: employee.full_name || employee.username,
        title: `Idle ${describeGap(idleMinutes)} today`,
        detail: `More than the ${describeGap(limit)} set in Configuration. `
              + "Idle time is measured, not guessed — check the day before acting on it.",
        minutes: idleMinutes,
    };
}

/** "3 hr 20 min", "45 min", "2 days". Short enough to sit in a row. */
function describeGap(minutes) {
    const total = Math.max(0, Math.round(Number(minutes) || 0));
    if (total < 60) return `${total} min`;
    if (total < 60 * 24) {
        const hours = Math.floor(total / 60);
        const rest = total % 60;
        return rest ? `${hours} hr ${rest} min` : `${hours} hr`;
    }
    const days = Math.floor(total / (60 * 24));
    const hours = Math.floor((total % (60 * 24)) / 60);
    return hours ? `${days} d ${hours} hr` : `${days} d`;
}

/**
 * Every alert for one employee, worst first.
 *
 * `facts` carries what the controller looked up. Anything missing simply
 * produces no alert of that kind rather than an error — a rule that cannot
 * be evaluated must stay quiet, not guess.
 */
function forEmployee(facts) {
    if (!setting(facts.settings, "alerts_enabled")) return [];
    // A suspended account is not a problem to be reported. It is a decision
    // somebody already made, and repeating it back every day is noise.
    if (facts.employee.suspended) return [];

    const found = [
        notReporting(facts),
        noLoginAfterShiftStart(facts),
        tooMuchIdle(facts),
    ].filter(Boolean);

    // Somebody whose app has been silent for two days has not "failed to log
    // in" as well — the second alert is the first one restated, and two rows
    // for one cause is how a list stops being read.
    const silent = found.some((a) => a.type === "NOT_REPORTING" || a.type === "NEVER_REPORTED");
    const kept = silent ? found.filter((a) => a.type !== "NO_LOGIN") : found;

    return kept.sort((a, b) => RANK[a.severity] - RANK[b.severity]);
}

module.exports = {
    DEFAULTS, SEVERITY,
    setting, forEmployee, describeGap,
    notReporting, noLoginAfterShiftStart, tooMuchIdle,
};
