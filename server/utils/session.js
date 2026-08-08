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

module.exports = { endSession, markLoggedIn };
