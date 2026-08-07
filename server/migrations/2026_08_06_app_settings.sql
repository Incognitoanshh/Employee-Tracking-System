-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — company-wide settings, starting with data retention
--
--  Nothing is purged today. retention_purge.sql exists with 90 and 180 days
--  hardcoded, and it is not in cron — so activity_logs and screenshots grow
--  without limit. At two employees that is 4 MB. At a thousand, ten captures
--  a day each, it is a couple of gigabytes a day until the disk fills and the
--  server stops.
--
--  Keeping every screenshot forever is also a liability rather than a
--  feature: a two-year-old capture of somebody's screen is a thing the
--  company has to protect and can be asked to account for.
--
--  WHY A SEPARATE TABLE
--  employee_configs is per employee, with a NULL row for the global default.
--  Retention is not per employee — one person cannot keep logs for a year
--  while another keeps them for a week, because the purge is one pass over
--  one table. Putting it there would create per-employee columns that must
--  never be set, which is the kind of thing somebody eventually sets.
--
--  Idempotent, and seeds the values the hardcoded script used so behaviour
--  does not change the moment this runs.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE IF NOT EXISTS app_settings (
    key        VARCHAR(64) PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_by VARCHAR(50)
);

INSERT INTO app_settings (key, value) VALUES
    ('log_retention_days',        '90'),
    ('screenshot_retention_days', '180'),
    ('attendance_retention_days', '730')
ON CONFLICT (key) DO NOTHING;

COMMIT;
