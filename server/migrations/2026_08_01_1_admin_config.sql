-- ─────────────────────────────────────────────────────────
-- Migration: Admin config support
-- Run karo: psql -U ansh -d ets_db -f migrations/add_admin_config.sql
-- ─────────────────────────────────────────────────────────

-- 1. screenshot_count column add karo
-- screenshot_count was renamed to screenshots_per_day by
-- 2026_08_04_screenshots_per_day.sql.
--
-- BUG this fixes: this ran unconditionally, so on a fresh install — where
-- ets.sql already creates screenshots_per_day — it added the OLD column back.
-- The rename then found its target already present and skipped, leaving both:
-- a dead screenshot_count that nothing reads, sitting next to the real one.
-- Two columns that look like the same setting is how somebody eventually
-- edits the wrong one.
--
-- Only added when the new name is absent, i.e. on a database old enough to
-- still need it.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'employee_configs' AND column_name = 'screenshots_per_day'
    ) THEN
        ALTER TABLE employee_configs
            ADD COLUMN IF NOT EXISTS screenshot_count INTEGER NOT NULL DEFAULT 3;
    END IF;
END$$;

-- 2. UNIQUE constraint on employee_id (upsert ke liye zaroori)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'employee_configs_employee_id_key'
    ) THEN
        ALTER TABLE employee_configs
            ADD CONSTRAINT employee_configs_employee_id_key UNIQUE (employee_id);
    END IF;
END$$;

-- 3. Global default row
-- BUG FIX: `ON CONFLICT (employee_id) DO NOTHING` NULL employee_id pe kabhi
-- trigger nahi hota (Postgres NULLs ko distinct maanta hai) — har run pe ek
-- nayi duplicate global row banti thi. Ab NOT EXISTS se guard karte hain.
-- The per-day screenshot column is not named here, and its default is used
-- instead — the same reason as in 2026_08_02_production_hardening.sql. Under
-- one name it does not exist on a fresh database, under the other it does not
-- exist on an old one, and naming either breaks the half this migration is
-- not looking at. On a fresh install ets.sql has already inserted this row,
-- so the guard below makes the whole statement a no-op anyway.
INSERT INTO employee_configs
    (employee_id, screenshot_min_minutes, screenshot_max_minutes,
     upload_interval_minutes, idle_threshold_seconds, force_logout)
SELECT NULL, 3, 10, 60, 60, false
WHERE NOT EXISTS (
    SELECT 1 FROM employee_configs WHERE employee_id IS NULL
);

-- 4. employees.role column confirm
ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'employee';
