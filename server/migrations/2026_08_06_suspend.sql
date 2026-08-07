-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — suspending an account
--
--  Force logout ends a session. It does not stop the person signing straight
--  back in, which makes it useless for the case it is reached for most: an
--  employee who should not be working right now — resigned, on notice, under
--  investigation, or simply not to be tracked this month.
--
--  Suspension is the state that persists. It survives sign-out, restart and
--  the token expiring, and only an administrator can lift it.
--
--  WHO MAY SUSPEND WHOM follows the hierarchy already used everywhere else
--  (canManage): an admin may suspend employees, a super admin may suspend
--  admins as well, and nobody may suspend a super admin.
--
--  suspended_at and suspended_by are kept because "why is this person locked
--  out" is asked weeks later, and an unexplained boolean is a bad answer.
--
--  Idempotent, and defaults to false so nobody is locked out by running it.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS suspended    BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMP WITHOUT TIME ZONE;
ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS suspended_by VARCHAR(50);

-- Login checks this on every attempt.
CREATE INDEX IF NOT EXISTS employees_suspended_idx ON employees (suspended)
    WHERE suspended;

COMMIT;
