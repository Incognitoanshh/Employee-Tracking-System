/**
 * How much of a shift was worked and how much was idle — from the events.
 *
 * ONE COPY, BECAUSE TWO WOULD DISAGREE. The employee's own page answers
 * "active 02:14:09, idle 00:41:52" and the tracker board answers the same
 * question about the same person at the same moment. Written twice, they
 * drift the first time somebody fixes one of them — and two screens
 * disagreeing about the same morning is the bug nobody can settle, because
 * neither number can be shown to be wrong.
 *
 * WHAT IT WORKS FROM. The shifts somebody was signed in for, and the
 * USER ACTIVE / USER IDLE lines their client wrote between them. Time is
 * counted from one event to the next, in whatever state the earlier one put
 * them — so a client that stops reporting does not silently accrue eight
 * hours of "active": the session's end is where the counting stops.
 *
 * SOMEBODY WHO JUST SIGNED IN IS ACTIVE. There is no event yet, and calling
 * that idle would open every morning with idle time nobody was idle for.
 *
 * NOTHING HERE READS A DATABASE. It is given rows and returns numbers, so
 * the arithmetic can be tested against an invented Tuesday.
 */

/** "USER IDLE (61.8s)" and "USER ACTIVE" are the two lines that matter. */
function normalizeState(activity) {
    const text = String(activity || "").toUpperCase();
    if (text.includes("USER IDLE")) return "IDLE";
    if (text.includes("USER ACTIVE")) return "ACTIVE";
    return null;
}

/**
 * Timestamps arrive as raw strings (db.js keeps them that way on purpose).
 * `new Date(str)` would read them in whatever timezone this process happens
 * to be in; these columns are UTC, so they are said to be UTC.
 */
function parseUtc(value) {
    if (value instanceof Date) return value.getTime();
    return new Date(String(value).replace(" ", "T") + "Z").getTime();
}

/**
 * @param {Array} sessions  [{ login_time, end_time }] — end_time already
 *                          resolved to NOW for a shift still open.
 * @param {Array} events    [{ created_at, activity }], any order.
 * @param {number} now      ms, so "how long in this state" is decided by the
 *                          caller rather than by the clock inside here.
 * @returns {{activeMs:number, idleMs:number, state:string|null,
 *            stateSinceMs:number|null, workingStreakMs:number}}
 */
function summarise(sessions, events, now = Date.now()) {
    const points = (events || [])
        .map((row) => ({ t: parseUtc(row.created_at), s: normalizeState(row.activity) }))
        .filter((event) => event.s && Number.isFinite(event.t))
        .sort((a, b) => a.t - b.t);

    let activeMs = 0;
    let idleMs = 0;
    let state = null;
    let stateSinceMs = null;
    // When the CURRENT unbroken stretch of working began. Not the same as
    // the last event: five ACTIVE lines in a row are one stretch, and a
    // person who has been at it for two hours should read as two hours.
    let streakStartMs = null;

    for (const session of sessions || []) {
        const start = parseUtc(session.login_time);
        const end = parseUtc(session.end_time);
        if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) continue;

        const inSession = points.filter((event) => event.t >= start && event.t <= end);

        let cursor = start;
        // Signed in and nothing said yet: at work.
        let current = "ACTIVE";
        let streak = start;

        for (const event of inSession) {
            const gap = event.t - cursor;
            if (gap > 0) {
                if (current === "ACTIVE") activeMs += gap;
                else idleMs += gap;
            }
            if (event.s !== current) {
                current = event.s;
                streak = event.t;
            }
            cursor = event.t;
        }

        const tail = end - cursor;
        if (tail > 0) {
            if (current === "ACTIVE") activeMs += tail;
            else idleMs += tail;
        }

        state = current;
        stateSinceMs = streak;
        streakStartMs = current === "ACTIVE" ? streak : null;
    }

    return {
        activeMs,
        idleMs,
        state,
        stateSinceMs,
        // Measured against the caller's `now` so an open shift reads as it
        // is at this moment rather than as it was at the last event.
        workingStreakMs: streakStartMs === null ? 0 : Math.max(0, now - streakStartMs),
        idleForMs: state === "IDLE" && stateSinceMs !== null
            ? Math.max(0, now - stateSinceMs) : 0,
    };
}

/** "02:14:09" — the shape both screens already show. */
function formatDuration(ms) {
    const seconds = Math.max(0, Math.floor(ms / 1000));
    const h = String(Math.floor(seconds / 3600)).padStart(2, "0");
    const m = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
    const s = String(seconds % 60).padStart(2, "0");
    return `${h}:${m}:${s}`;
}

module.exports = { summarise, formatDuration, normalizeState, parseUtc };
