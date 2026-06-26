-- Migration: Create audit_logs table and add last_ping column to active_sessions
-- Run: psql -U ansh -d ets_db -f migrations/create_audit_logs.sql

CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    employee_id VARCHAR(50),
    activity TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    CONSTRAINT fk_audit_logs_employee FOREIGN KEY (employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE
);

ALTER TABLE active_sessions ADD COLUMN IF NOT EXISTS last_ping TIMESTAMP WITH TIME ZONE DEFAULT NOW();
