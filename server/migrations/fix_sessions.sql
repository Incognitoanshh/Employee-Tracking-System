CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    employee_id TEXT NOT NULL,
    auth_token TEXT,
    device_id TEXT,
    login_time TIMESTAMP DEFAULT NOW(),
    logout_time TIMESTAMP,
    shift_start TEXT,
    shift_end TEXT,
    status TEXT DEFAULT 'ACTIVE',
    FOREIGN KEY (employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_employee ON sessions(employee_id, status);
