-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — one account, one machine at a time
--
--  Signing in from a second machine used to take the account over and throw
--  the first one out. Two people sharing one login could work all day, each
--  quietly evicting the other, and the attendance record would read as a
--  single person.
--
--  The obvious fix — refuse the second login while a session exists — locks
--  people out of their own accounts. An app that crashes, a laptop that is
--  closed, a machine that loses power: none of those log out, so the row
--  stays and the employee can never sign in again.
--
--  So the rule is about a LIVE session, not a session row. last_seen is
--  stamped by every authenticated request (throttled, see auth.middleware),
--  and a client polls the server every five seconds. A session that has not
--  been seen for a couple of minutes is a dead one and can be taken over.
--
--  Backfilled from login_time so existing rows are not treated as live
--  forever by a NULL comparison.
--
--  Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE active_sessions
    ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP WITH TIME ZONE;

UPDATE active_sessions SET last_seen = login_time WHERE last_seen IS NULL;

COMMIT;
