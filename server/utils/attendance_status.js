/**
 * Was this login on time, late, or on a day nobody was meant to work?
 *
 * Nothing here is stored. The status is derived every time it is read, from
 * the shift the employee has NOW. That is deliberate: a shift corrected today
 * fixes last month's history with it, instead of leaving rows stamped against
 * a rule that no longer applies. The cost is that changing a shift rewrites
 * how the past reads, which is the right trade for a small company where a
 * wrong shift is far more likely than a deliberate mid-history change.
 *
 * The whole thing works in IST minutes-since-midnight. Callers pass values
 * PostgreSQL has already converted, so no timezone arithmetic happens in
 * JavaScript — that split is what kept the timezone bugs in this project
 * confined to one layer at a time.
 */

/** "09:00" or "09:00:00" -> 540. Null for anything unparseable. */
function toMinutes(hhmm) {
    const match = /^(\d{1,2}):(\d{2})/.exec(String(hhmm || "").trim());
    if (!match) return null;
    const hours = Number(match[1]);
    const mins = Number(match[2]);
    if (hours > 23 || mins > 59) return null;
    return hours * 60 + mins;
}

const DAY = 24 * 60;

/**
 * Which calendar day's shift does a login at `loginMinutes` belong to?
 *
 * Only ever anything other than "its own day" for an overnight shift. A
 * 22:00-06:00 shift running past midnight means a 02:00 login belongs to the
 * PREVIOUS day's shift — the same rule the client's scheduler applies, and
 * for the same reason: without it every night worker looks like they arrived
 * sixteen hours early.
 *
 * @returns {number} 0 for the login's own day, -1 for the day before.
 */
function shiftDayOffset(loginMinutes, startMinutes, endMinutes) {
    const overnight = endMinutes <= startMinutes;
    if (!overnight) return 0;
    return loginMinutes < endMinutes ? -1 : 0;
}

/**
 * @param {object} input
 * @param {number} input.loginMinutes   IST minutes since midnight.
 * @param {string} input.shiftStart     "HH:MM" IST, or null if not configured.
 * @param {string} input.shiftEnd       "HH:MM" IST, or null.
 * @param {number} input.graceMinutes   Lateness not worth flagging.
 * @param {boolean} input.isDayOff      Weekly off or holiday, already resolved
 *                                      for the day the SHIFT belongs to.
 *
 * @returns {{status: string, late_minutes: number|null, label: string}}
 *   status is one of: day_off, late, on_time, outside_shift, unknown.
 *   `label` is what the table shows, so the client never has to build one.
 */
function classifyLogin({ loginMinutes, shiftStart, shiftEnd, graceMinutes, isDayOff }) {
    const start = toMinutes(shiftStart);
    const end = toMinutes(shiftEnd);
    const grace = Number.isFinite(graceMinutes) ? Math.max(0, graceMinutes) : 10;

    // A day off outranks everything: if nobody was meant to work, arriving at
    // 11:00 is not "late", and calling it late would be the sort of thing that
    // costs somebody a day's pay.
    if (isDayOff) {
        return { status: "day_off", late_minutes: null, label: "Day off" };
    }

    // No usable shift means no baseline to be late against. Saying so beats
    // defaulting to 09:00 and quietly marking a night shift late every day.
    if (start === null || end === null) {
        // NAMED, not a dash. An administrator seeing "—" has no way to tell
        // this from a reconnect, from a page that failed to load, or from a
        // bug — and the answer here is something they can actually act on:
        // give this person a shift and the column starts working.
        return { status: "unknown", late_minutes: null, label: "No shift set" };
    }

    const offset = shiftDayOffset(loginMinutes, start, end);
    // Re-base the login onto the shift's own timeline. For an overnight shift
    // a 02:00 login becomes minute 1560, which is genuinely 4 hours after a
    // 22:00 start rather than 20 hours before it.
    const loginOnShiftDay = loginMinutes - offset * DAY;
    const shiftEndOnShiftDay = end <= start ? end + DAY : end;

    const delta = loginOnShiftDay - start;

    if (loginOnShiftDay > shiftEndOnShiftDay) {
        // Signing in after the shift has finished is not lateness, and
        // reporting it as "late by 14 hours" would be worse than useless.
        return { status: "outside_shift", late_minutes: null, label: "Outside shift" };
    }
    if (delta > grace) {
        return { status: "late", late_minutes: delta, label: `Late ${formatGap(delta)}` };
    }
    return { status: "on_time", late_minutes: null, label: "On time" };
}

/** 40 -> "40m", 95 -> "1h 35m". */
function formatGap(minutes) {
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

/**
 * Is `isoDate` a non-working day for someone with these weekly offs?
 * `weeklyOffs` is the stored "6,7" form; `holidays` a Set of YYYY-MM-DD.
 */
function isNonWorkingDay(isoDate, weeklyOffs, holidays) {
    if (holidays && holidays.has(isoDate)) return true;
    const parts = String(weeklyOffs || "")
        .split(",")
        .map((piece) => parseInt(piece.trim(), 10))
        .filter((n) => Number.isInteger(n) && n >= 1 && n <= 7);
    if (parts.length === 0) return false;
    // getUTCDay: 0 = Sunday. ISO wants 7 for Sunday, matching the client and
    // the stored values. Parsed as UTC so the server's own timezone cannot
    // shift the weekday by one.
    const day = new Date(`${isoDate}T00:00:00Z`).getUTCDay();
    return parts.includes(day === 0 ? 7 : day);
}

/** Move an ISO date by whole days, staying in UTC so no zone can shift it. */
function shiftIsoDate(isoDate, days) {
    const date = new Date(`${isoDate}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() + days);
    return date.toISOString().slice(0, 10);
}

module.exports = {
    toMinutes,
    shiftDayOffset,
    classifyLogin,
    isNonWorkingDay,
    shiftIsoDate,
    formatGap,
};
