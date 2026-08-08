-- ═══════════════════════════════════════════════════════════════════════════
--  One login at a time, as a flag anybody can read.
--
--  WHY THE DEVICE ID WAS NOT ENOUGH
--  The previous rule let the SAME machine take its session back, matched by a
--  device id the client generates. That is the right rule when a company hands
--  out the laptops. Here people use their own, and a device id that changes —
--  a reinstall, a new machine, a wiped data directory — makes somebody look
--  like a second person and locks them out of their own account.
--
--  So the rule is now simply: logged in somewhere, or not.
--
--  THE FLAG IS DERIVED, NOT DUPLICATED. `is_logged_in` is maintained in the
--  same statements that set and clear active_sessions.token — never on its
--  own. Two independent copies of one fact drift, and the drift here would be
--  either "logged in and cannot log in anywhere" or "logged in twice", which
--  are the only two failures this whole mechanism exists to prevent.
--
--  WHAT HAPPENS WHEN AN APP DIES WITHOUT LOGGING OUT
--  Nothing calls logout on a crash, a flat battery or a killed process. A bare
--  flag would leave that person locked out until an administrator intervened —
--  which is exactly the bug the device id was introduced to fix, and it would
--  be a support call every week on personal machines.
--
--  So the flag is read together with the heartbeat: a session that has not
--  been heard from for LOGIN_STALE_MINUTES is not a session, it is a machine
--  that went away. Two machines at once remain impossible, because a live one
--  reports in every few seconds. A crash heals itself in about two minutes.
--
--  Run: psql ... -f migrations/2026_08_08_3_login_flag.sql
--  Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS is_logged_in BOOLEAN NOT NULL DEFAULT FALSE;

-- Bring existing rows in line with the sessions that are already open, so the
-- flag is not false for people who are working when this runs.
UPDATE employees e
   SET is_logged_in = EXISTS (
       SELECT 1 FROM active_sessions s
        WHERE s.employee_id = e.employee_id AND s.token IS NOT NULL
   );

-- The admin panel asks "who is signed in" on every draw.
CREATE INDEX IF NOT EXISTS employees_logged_in_idx
    ON employees (is_logged_in)
    WHERE is_logged_in;

COMMIT;
