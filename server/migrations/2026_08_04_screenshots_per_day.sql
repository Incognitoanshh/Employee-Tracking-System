-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — screenshot_count -> screenshots_per_day
--
--  The value used to mean "captures per shift". It now means "captures per
--  IST calendar day", enforced by a hard cap on the client.
--
--  WHY THE RENAME
--  The old name was actively misleading. Because the count was tied to the
--  shift, an employee who stayed logged in outside their shift fell onto a
--  separate code path that ignored the count entirely and captured every
--  min..max minutes. Measured in production: 167 captures in 24h and 334 in
--  48h where the admin had configured 10. Renaming the column keeps the
--  database honest about what the number now guarantees.
--
--  Values carry over unchanged — 10 per shift becomes 10 per day, which is
--  the same or fewer captures, never more.
--
--  Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'employee_configs' AND column_name = 'screenshot_count'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'employee_configs' AND column_name = 'screenshots_per_day'
    ) THEN
        ALTER TABLE employee_configs RENAME COLUMN screenshot_count TO screenshots_per_day;
        RAISE NOTICE 'renamed screenshot_count -> screenshots_per_day';
    ELSIF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'employee_configs' AND column_name = 'screenshots_per_day'
    ) THEN
        ALTER TABLE employee_configs ADD COLUMN screenshots_per_day INTEGER DEFAULT 10;
        RAISE NOTICE 'added screenshots_per_day';
    ELSE
        RAISE NOTICE 'screenshots_per_day already present — nothing to do';
    END IF;
END $$;

-- The old per-shift default of 3 is low for a whole day; anything already
-- configured is left exactly as the admin set it.
UPDATE employee_configs SET screenshots_per_day = 10
WHERE screenshots_per_day IS NULL;

COMMIT;
