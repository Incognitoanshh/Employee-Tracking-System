-- Migration: verbose_logging column add karo employee_configs mein
-- Run karo: psql -U <user> -d ets_db -f migrations/add_verbose_logging.sql

ALTER TABLE employee_configs
    ADD COLUMN IF NOT EXISTS verbose_logging BOOLEAN NOT NULL DEFAULT false;
