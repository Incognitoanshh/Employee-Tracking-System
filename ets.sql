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
    screenshot_count        INTEGER  NOT NULL DEFAULT 3,
    upload_interval_minutes INTEGER  NOT NULL DEFAULT 60,
    idle_threshold_seconds  INTEGER  NOT NULL DEFAULT 60,
    force_logout            BOOLEAN  NOT NULL DEFAULT false,
    verbose_logging         BOOLEAN  NOT NULL DEFAULT false,
    created_at              TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    updated_at              TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

-- Global default config row
INSERT INTO employee_configs
    (employee_id, screenshot_min_minutes, screenshot_max_minutes,
     screenshot_count, upload_interval_minutes, idle_threshold_seconds,
     force_logout, verbose_logging)
VALUES
    (NULL, 3, 10, 3, 60, 60, false, false)
ON CONFLICT (employee_id) DO NOTHING;

-- ═══════════════════════════════════════════════════════════════
-- MIGRATION: existing DB pe run karo agar already tables hain
-- ═══════════════════════════════════════════════════════════════
ALTER TABLE employee_configs
    ADD COLUMN IF NOT EXISTS screenshot_count   INTEGER NOT NULL DEFAULT 3;
ALTER TABLE employee_configs
    ADD COLUMN IF NOT EXISTS verbose_logging    BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'employee';
