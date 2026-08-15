/**
 * Ending somebody's session — in one place, because it is two writes.
 *
 * A session is over when `active_sessions.token` is NULL. `employees.is_logged_in`
 * says the same thing in a form the admin panel and the login check can read
 * cheaply. Two facts, one truth: if they ever disagree the result is either
 * somebody locked out of their own account, or somebody signed in twice — the
 * only two failures the single-login rule exists to prevent.
 *
 * Seven places ended a session before this existed: logout, refresh finding a
 * stale row, a password reset, a role change, suspension, force logout, and
 * deleting an employee. Adding a second write to seven call sites is six
 * chances to forget one, and the one that is forgotten fails silently.
 *
 * TAKES A QUERYABLE rather than reaching for the pool. Several callers are
 * inside a transaction and must not have this land outside it — deleting an
 * employee, in particular, rolls back as a whole or not at all.
 */

/**
 * @param {object} db a pg Pool or a checked-out Client
 * @param {string} employeeId
 */
/**
 * End the session THIS TOKEN belongs to, and no other.
 *
 * WHY THIS EXISTS SEPARATELY. Signing out cleared the row by employee_id
 * alone, so any instance quitting ended whatever session was current — even
 * one belonging to a different machine, opened minutes later.
 *
 * Seen exactly that way: an older build was still running when the new one
 * was installed. The new build signed in at 10:06:39; the single-session rule
 * pushed the old one out; the old one shut down cleanly at 10:07:41 and sent
 * its logout on the way. The server matched on employee_id, cleared the row,
 * and killed the session that had replaced it. The app on screen was left
 * holding a token the server would now reject, and presence counted the
 * person as offline while they were plainly working — which is the shape of
 * an older complaint too: "yaha active dikh raha, yaha offline".
 *
 * A logout is a statement about the session making it. Matching the token
 * makes it exactly that, and a stale one now clears nothing.
 *
 * NOT for force-logout, logout-everywhere, password reset or suspend: those
 * mean "end whatever is current, whoever holds it", which is endSession.
 */
async function endSessionForToken(db, employeeId, token) {
    const result = await db.query(
        `UPDATE active_sessions SET token = NULL
          WHERE employee_id = $1 AND token = $2`,
        [employeeId, token]
    );
    // is_logged_in only follows a session that was actually ended. Clearing it
    // regardless would put the flag out of step with the row it describes, and
    // that flag is what the employee list reads.
    if (result.rowCount > 0) {
        await db.query(
            `UPDATE employees SET is_logged_in = FALSE WHERE employee_id = $1`,
            [employeeId]
        );
    }
    return result.rowCount > 0;
}

async function endSession(db, employeeId) {
    // The row is kept and blanked rather than deleted. verifyToken only
    // rejects an old token when a row EXISTS with a different one; with no row
    // at all that check is skipped, and a token already in flight would keep
    // working until it expired on its own.
    await db.query(
        `UPDATE active_sessions SET token = NULL WHERE employee_id = $1`,
        [employeeId]
    );
    await db.query(
        `UPDATE employees SET is_logged_in = FALSE WHERE employee_id = $1`,
        [employeeId]
    );
}

/** The other direction, for completeness — used by login. */
async function markLoggedIn(db, employeeId) {
    await db.query(
        `UPDATE employees SET is_logged_in = TRUE WHERE employee_id = $1`,
        [employeeId]
    );
}

module.exports = { endSession, endSessionForToken, markLoggedIn };
