-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — remember WHICH machine a session belongs to
--
--  One account, one machine at a time. That was already the rule, but it was
--  enforced by asking only "is there a live session?" — with no idea whose
--  machine it was on. So closing the app without signing out locked you out
--  of your OWN laptop until the token expired: the session was still live,
--  the login looked like a second machine, and it was refused.
--
--  The client has always sent a device_id with the login. Storing it here is
--  what lets the two cases be told apart:
--
--    same device_id   -> the same machine coming back. Replace the session.
--    different one    -> a second machine. Refuse, as intended.
--
--  Existing rows get NULL, which is treated as "unknown machine" and refused
--  — the safe direction. One sign-out clears it and the next login records
--  the device properly.
--
--  NOTE ON PERMISSIONS: run this as the OWNER of active_sessions. On the
--  production database the application's role cannot ALTER tables it does not
--  own, and psql reports "must be owner of table active_sessions" — which,
--  run in a loop that only checks the exit code, is easy to miss until
--  something stops working.
--
--  Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE active_sessions ADD COLUMN IF NOT EXISTS device_id TEXT;

COMMIT;
