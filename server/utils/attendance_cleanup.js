/**
 * Closing a shift that nobody ever closed.
 *
 * THE DAMAGE THIS UNDOES, seen in the customer's own attendance list:
 *
 *     33  TEST001  08 Aug 01:43:41 PM -> 12 Aug 12:22:03 PM   94:38:22
 *
 * Ninety-four hours and thirty-eight minutes recorded as one shift. Nobody
 * worked it. The app was closed on the 8th without signing out, so the row
 * stayed open — and the next login, four days later, closed it and computed
 * the total from the two timestamps it had. That number goes into the
 * timesheet and the attendance report.
 *
 * It also made two screens disagree about the same row: Attendance said
 * ACTIVE, because the rule there is "no logout_time", while the employee list
 * said Offline, because presence treats a row older than a full shift as
 * abandoned. One of them had to be wrong, and it was Attendance.
 *
 * WHAT THIS DOES INSTEAD. A row is closed at the last moment there is any
 * evidence the person was actually there — the session heartbeat, an activity
 * line, a screenshot — rather than at whatever time somebody happened to log
 * in next. If there is no evidence at all beyond the login itself, it closes
 * at the login time, which records a shift of zero rather than a fiction.
 *
 * WHAT IT WILL NOT TOUCH:
 *
 *   * a row younger than a full shift — somebody may still be working it;
 *   * a row whose session is alive — the app is running and being heard from,
 *     which is the whole definition of at work, however long the shift has
 *     run.
 *
 * Both conditions must hold before anything is written, so an employee on a
 * long overnight shift, or one whose network dropped for ten minutes, is
 * never signed out by this.
 */
const { HEARTBEAT_GRACE_MINUTES, MAX_SHIFT_HOURS } = require("./presence");

/**
 * @param {import("pg").Pool} pool
 * @returns {Promise<number>} how many rows were closed
 */
async function closeAbandonedShifts(pool) {
    // `active_sessions` is the only table that stores a timezone-aware
    // timestamp; everything else is naive UTC. `AT TIME ZONE 'UTC'` converts
    // the one into the other, and mixing them without it is a silent
    // five-and-a-half-hour error.
    const result = await pool.query(`
        UPDATE attendance a
           SET logout_time = ev.ended,
               total_hours = ev.ended - a.login_time
          FROM (
            SELECT a2.id,
                   GREATEST(
                       a2.login_time,
                       COALESCE(s.last_seen AT TIME ZONE 'UTC', a2.login_time),
                       COALESCE((SELECT MAX(al.created_at) FROM activity_logs al
                                  WHERE al.employee_id = a2.employee_id
                                    AND al.created_at >= a2.login_time),
                                a2.login_time),
                       COALESCE((SELECT MAX(sc.created_at) FROM screenshots sc
                                  WHERE sc.employee_id = a2.employee_id
                                    AND sc.created_at >= a2.login_time),
                                a2.login_time)
                   ) AS ended
              FROM attendance a2
              LEFT JOIN active_sessions s ON s.employee_id = a2.employee_id
             WHERE a2.logout_time IS NULL
               AND a2.login_time < (NOW() AT TIME ZONE 'UTC')
                                   - INTERVAL '${MAX_SHIFT_HOURS} hours'
               AND (s.last_seen IS NULL
                    OR s.last_seen < NOW()
                                     - INTERVAL '${HEARTBEAT_GRACE_MINUTES} minutes')
          ) ev
         WHERE a.id = ev.id
    `);
    return result.rowCount || 0;
}

/**
 * Close ONE person's open shift, now, at the last evidence they were there.
 *
 * For force logout. An administrator ending somebody's session is ending
 * their working session — but the row that records it was only ever closed by
 * the client, which posts to /attendance/logout as it shuts down. After a
 * force logout that post arrives with a token the server has just
 * invalidated, so it is refused, and the row stays open.
 *
 * What that looked like: Attendance showing ACTIVE for somebody who had been
 * signed out by an admin and whose app was closed, until the sixteen-hour
 * sweep eventually caught it. The same disagreement between two screens that
 * closeAbandonedShifts above was written to end.
 *
 * CLOSED AT THE LAST EVIDENCE, not at the moment the button was pressed.
 * Somebody force-logged-out at six in the evening, whose machine went quiet
 * at two, worked until two — recording four extra hours because an
 * administrator clicked later would be a fiction in the timesheet, and the
 * timesheet is what people are paid from.
 */
async function closeShiftFor(db, employeeId) {
    const result = await db.query(`
        UPDATE attendance a
           SET logout_time = ended,
               total_hours = ended - a.login_time
          FROM (
            SELECT a2.id,
                   GREATEST(
                       a2.login_time,
                       COALESCE(s.last_seen AT TIME ZONE 'UTC', a2.login_time),
                       COALESCE((SELECT MAX(al.created_at) FROM activity_logs al
                                  WHERE al.employee_id = a2.employee_id
                                    AND al.created_at >= a2.login_time),
                                a2.login_time),
                       COALESCE((SELECT MAX(sc.created_at) FROM screenshots sc
                                  WHERE sc.employee_id = a2.employee_id
                                    AND sc.created_at >= a2.login_time),
                                a2.login_time)
                   ) AS ended
              FROM attendance a2
              LEFT JOIN active_sessions s ON s.employee_id = a2.employee_id
             WHERE a2.employee_id = $1
               AND a2.logout_time IS NULL
          ) evidence
         WHERE a.id = evidence.id`,
        [employeeId]);
    return result.rowCount;
}

module.exports = { closeAbandonedShifts, closeShiftFor };
