-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — keep the evidence longer than the noise
--
--  activity_logs holds two different things under one name. Almost all of it
--  is volume: idle and active flips, sign-ins, scheduler chatter. A small
--  part records what an administrator did — reset a password, delete
--  somebody's screenshots, change a retention period.
--
--  One period cannot serve both. Thirty-one days keeps the table small and
--  is right for the noise; applied to the rest it means that two months on,
--  "who deleted those screenshots" has no answer anywhere.
--
--  So there are two periods now. The existing log_retention_days keeps its
--  meaning and applies to the noise; audit_log_retention_days covers the
--  administrative actions and defaults to two years — long enough to answer
--  a question raised well after the fact.
--
--  Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

INSERT INTO app_settings (key, value) VALUES
    ('audit_log_retention_days', '730')
ON CONFLICT (key) DO NOTHING;

COMMIT;
