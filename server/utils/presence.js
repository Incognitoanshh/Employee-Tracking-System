/**
 * One definition of "this person is online right now".
 *
 * THE BUG THIS EXISTS TO FIX
 * Online used to mean nothing more than "has an attendance row with no
 * logout_time, opened in the last sixteen hours". That is a record of having
 * STARTED work, not evidence of anything still running. So any app that ended
 * without a clean logout — a crash, a force-logout, a password reset clearing
 * the token, a laptop lid closed, being signed out by a login from another
 * machine — left the person showing a green Online dot for up to sixteen
 * hours after they were gone.
 *
 * It was found by looking at the panel: an employee sat on Online with no row
 * in active_sessions at all. The two tables disagreed and the panel believed
 * the wrong one. For a product whose whole job is saying who is working, that
 * is close to the worst kind of wrong answer — it is confidently false, and
 * an administrator has no way to tell.
 *
 * THE RULE NOW
 *   Online = an attendance row still open (they started work and have not
 *            finished), AND a session that has been heard from recently
 *            (something is actually running).
 *
 * Both halves are needed. The attendance row alone is the bug above. The
 * session alone would call somebody online after their shift ended, while
 * the app sits in the tray overnight.
 *
 * WHY FIVE MINUTES
 * The heartbeat is stamped by auth.middleware on any authenticated request,
 * throttled to once a minute. Clients poll every five seconds, but back off
 * to two minutes when the network is failing — which on this connection is
 * ordinary, not exceptional. One minute of throttle plus two of backoff is
 * three; five leaves room without letting a dead app linger for long.
 *
 * Written once, here, because two controllers ask this question — the
 * employee list and the dashboard's online count — and if they answer it
 * differently the same screen shows a person online in one place and offline
 * in another.
 */

/** How long a session may go unheard before it stops counting as alive. */
const HEARTBEAT_GRACE_MINUTES = 5;

/**
 * A stale attendance row is one nobody ever closed. Sixteen hours is longer
 * than any real shift, so past that the row is abandoned rather than open.
 * Kept from the original rule — it is still a useful backstop for a machine
 * that has been off for days.
 */
const MAX_SHIFT_HOURS = 16;

/**
 * SQL that is true when the employee in `alias` is genuinely online.
 *
 * @param {string} alias table alias for the employees row in scope, whose
 *   `employee_id` column is used. Not interpolated from user input anywhere —
 *   every caller passes a literal.
 */
function isOnlineSql(alias = "e") {
    return `(
        CASE WHEN ${alias}.role IN ('admin', 'super_admin') THEN
            -- AN ADMINISTRATOR HAS NO SHIFT, so there is no attendance row to
            -- look for and never will be — they are not a tracked employee.
            -- Requiring one made an admin read "Offline" while they were
            -- plainly using the panel, with "Last seen: just now" beside it,
            -- which is two statements that cannot both be true. Reported from
            -- a real installed build.
            --
            -- For them the honest question is the other one: are they signed
            -- in right now.
            ${liveSessionSql(alias)}
        ELSE
        EXISTS (
            SELECT 1 FROM attendance att
             WHERE att.employee_id = ${alias}.employee_id
               AND att.logout_time IS NULL
               -- attendance.login_time is TIMESTAMP WITHOUT TIME ZONE holding
               -- UTC, so the other side of the comparison must be made naive
               -- UTC too.
               AND att.login_time > (NOW() AT TIME ZONE 'UTC')
                                    - INTERVAL '${MAX_SHIFT_HOURS} hours'
        )
        AND ${liveSessionSql(alias)}
        END
    )`;
}

/**
 * Has this person's app been heard from recently?
 *
 * Split out because it is now asked on its own for administrators and as half
 * the answer for everybody else — and one definition of "the app is running"
 * is better than two that can drift.
 */
function liveSessionSql(alias) {
    return `EXISTS (
            SELECT 1 FROM active_sessions ses
             WHERE ses.employee_id = ${alias}.employee_id
               AND ses.token IS NOT NULL
               -- PLAIN NOW(), and the difference matters.
               --
               -- active_sessions is the ONLY table in this database whose
               -- timestamps carry a time zone; every other one is naive UTC.
               -- So this comparison must be aware-against-aware, while the
               -- one above must be naive-against-naive.
               --
               -- Writing '(NOW() AT TIME ZONE ''UTC'')' here would still work
               -- today, because config/db.js pins the connection to UTC and
               -- Postgres would coerce the naive side back correctly. It
               -- would break silently — by five and a half hours — the moment
               -- that option went away or the query ran from any other
               -- session. Exactly the trap utils/ist_sql.js was written for.
               AND ses.last_seen > NOW()
                                   - INTERVAL '${HEARTBEAT_GRACE_MINUTES} minutes'
        )`;
}

module.exports = { isOnlineSql, HEARTBEAT_GRACE_MINUTES, MAX_SHIFT_HOURS };
