-- ─────────────────────────────────────────────────────────
-- Migration: Admin config support
-- Run karo: psql -U ansh -d ets_db -f migrations/add_admin_config.sql
-- ─────────────────────────────────────────────────────────

-- 1. screenshot_count column add karo
ALTER TABLE employee_configs
    ADD COLUMN IF NOT EXISTS screenshot_count INTEGER NOT NULL DEFAULT 3;

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
INSERT INTO employee_configs
    (employee_id, screenshot_min_minutes, screenshot_max_minutes,
     screenshot_count, upload_interval_minutes, idle_threshold_seconds, force_logout)
VALUES
    (NULL, 3, 10, 3, 60, 60, false)
ON CONFLICT (employee_id) DO NOTHING;

-- 4. employees.role column confirm
ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'employee';
