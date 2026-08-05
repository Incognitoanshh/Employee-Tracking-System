-- ═══════════════════════════════════════════════════════════════
-- ETS — PostgreSQL Schema (Fresh Install)
-- Usage: psql -U <user> -d ets_db -f ets.sql
-- ═══════════════════════════════════════════════════════════════

-- employees
CREATE TABLE IF NOT EXISTS employees (
    id          SERIAL PRIMARY KEY,
    employee_id VARCHAR(50)  UNIQUE NOT NULL,
    username    VARCHAR(100) UNIQUE NOT NULL,
    password    VARCHAR(255) NOT NULL,
    role        VARCHAR(20)  NOT NULL DEFAULT 'employee',
    full_name   VARCHAR(120),
    designation VARCHAR(120),
    created_at  TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

-- active_sessions (one row per employee — upsert on login)
CREATE TABLE IF NOT EXISTS active_sessions (
    employee_id TEXT PRIMARY KEY,
    token       TEXT,
    login_time  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- attendance
CREATE TABLE IF NOT EXISTS attendance (
    id          SERIAL PRIMARY KEY,
    employee_id VARCHAR(50) NOT NULL,
    login_time  TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    logout_time TIMESTAMP WITHOUT TIME ZONE,
    total_hours INTERVAL,
    created_at  TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

-- screenshots
CREATE TABLE IF NOT EXISTS screenshots (
    id          SERIAL PRIMARY KEY,
    employee_id VARCHAR(50),
    file_name   TEXT,
    created_at  TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

-- activity_logs
CREATE TABLE IF NOT EXISTS activity_logs (
    id          SERIAL PRIMARY KEY,
    employee_id VARCHAR(50),
    activity    TEXT,
    created_at  TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

-- employee_configs
-- BUG FIX: verbose_logging column add kiya — pehle missing tha, server crash karta tha
CREATE TABLE IF NOT EXISTS employee_configs (
    id                      SERIAL PRIMARY KEY,
    employee_id             VARCHAR(50) UNIQUE,   -- NULL = global default
    screenshot_min_minutes  INTEGER  NOT NULL DEFAULT 3,
    screenshot_max_minutes  INTEGER  NOT NULL DEFAULT 10,
    screenshots_per_day     INTEGER  NOT NULL DEFAULT 10,
    upload_interval_minutes INTEGER  NOT NULL DEFAULT 60,
    idle_threshold_seconds  INTEGER  NOT NULL DEFAULT 60,
    force_logout            BOOLEAN  NOT NULL DEFAULT false,
    verbose_logging         BOOLEAN  NOT NULL DEFAULT false,
    created_at              TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at              TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

-- screenshots_per_day — was screenshot_count, and meant "per shift".
--
-- This has to run BEFORE the global-default INSERT below, which names
-- screenshots_per_day. On a server still on the old schema the INSERT would
-- otherwise fail on a column that has not been renamed yet, and ON_ERROR_STOP
-- would abort the whole file — leaving that database on the old schema with
-- no sign anything went wrong beyond one line of psql output.
--
-- The lines this replaces read `ADD COLUMN IF NOT EXISTS screenshot_count`,
-- which on an already-migrated database added the OLD column straight back
-- next to the live one. Nothing read it, which is why nobody would notice.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'employee_configs' AND column_name = 'screenshot_count')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'employee_configs' AND column_name = 'screenshots_per_day')
    THEN
        ALTER TABLE employee_configs RENAME COLUMN screenshot_count TO screenshots_per_day;
    END IF;
END $$;
ALTER TABLE employee_configs
    ADD COLUMN IF NOT EXISTS screenshots_per_day INTEGER NOT NULL DEFAULT 10;

-- Sirf EK global-default row ho sake. Normal UNIQUE constraint yahan kaam
-- nahi karta: Postgres NULLs ko ek doosre se DISTINCT maanta hai, is liye
-- `ON CONFLICT (employee_id) DO NOTHING` NULL row pe kabhi trigger hi nahi
-- hota tha — har baar ye file chalane pe ek NAYI duplicate global row ban
-- jaati thi. Partial unique index NULL pe bhi enforce karta hai.
CREATE UNIQUE INDEX IF NOT EXISTS employee_configs_single_global
    ON employee_configs ((employee_id IS NULL))
    WHERE employee_id IS NULL;

-- Global default config row (sirf tab jab pehle se na ho)
INSERT INTO employee_configs
    (employee_id, screenshot_min_minutes, screenshot_max_minutes,
     screenshots_per_day, upload_interval_minutes, idle_threshold_seconds,
     force_logout, verbose_logging)
SELECT NULL, 3, 10, 10, 60, 60, false, false
WHERE NOT EXISTS (
    SELECT 1 FROM employee_configs WHERE employee_id IS NULL
);

-- ═══════════════════════════════════════════════════════════════
-- MIGRATION: existing DB pe run karo agar already tables hain
-- ═══════════════════════════════════════════════════════════════
ALTER TABLE employee_configs
    ADD COLUMN IF NOT EXISTS verbose_logging    BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'employee';

-- shift_start/shift_end columns (admin panel se set hoti hain)
ALTER TABLE employee_configs ADD COLUMN IF NOT EXISTS shift_start TIME DEFAULT '09:00';
ALTER TABLE employee_configs ADD COLUMN IF NOT EXISTS shift_end   TIME DEFAULT '18:00';

-- verbose_logging column
ALTER TABLE employee_configs ADD COLUMN IF NOT EXISTS verbose_logging BOOLEAN NOT NULL DEFAULT false;

-- Password change and reset (2026_08_05_password_management.sql)
ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS password_changed_at  TIMESTAMP WITHOUT TIME ZONE;

-- Weekly offs and holidays (2026_08_05_work_calendar.sql)
ALTER TABLE employee_configs
    ADD COLUMN IF NOT EXISTS weekly_offs VARCHAR(20) NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS holidays (
    id           SERIAL PRIMARY KEY,
    holiday_date DATE         NOT NULL UNIQUE,
    name         VARCHAR(120) NOT NULL,
    created_by   VARCHAR(50),
    created_at   TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS holidays_date_idx ON holidays (holiday_date);
