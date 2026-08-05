-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — idle time per day
--
--  Reports shipped without an idle column on purpose. activity_logs records
--  the MOMENT somebody went idle or active, so a daily total means pairing
--  each IDLE with the ACTIVE that follows it — and a crash, a dropped
--  connection or a logout leaves a pair open forever. The total would be
--  wrong by an unknown amount, and a wrong idle figure in a payroll report
--  is worse than no figure.
--
--  The client now accumulates instead. Its idle tracker already asks the
--  operating system how long the machine has been idle, every two seconds,
--  so the time is simply added up as it passes: a crash costs one tick and
--  leaves nothing open to go wrong.
--
--  One row per employee per IST day. The client re-sends a day whenever its
--  total grows, so this is an upsert and the value only ever moves forward.
--
--  Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE IF NOT EXISTS idle_daily (
    employee_id  VARCHAR(50) NOT NULL,
    day          DATE        NOT NULL,
    idle_seconds INTEGER     NOT NULL DEFAULT 0,
    updated_at   TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (employee_id, day)
);

-- Reports read this as "every employee, over a date range".
CREATE INDEX IF NOT EXISTS idle_daily_day_idx ON idle_daily (day);

COMMIT;
