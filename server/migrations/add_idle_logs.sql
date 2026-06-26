CREATE TABLE IF NOT EXISTS idle_logs (
    id SERIAL PRIMARY KEY,
    employee_id TEXT NOT NULL,
    session_id INTEGER,
    idle_start TIMESTAMP,
    idle_end TIMESTAMP,
    duration_seconds INTEGER,
    upload_status TEXT DEFAULT 'PENDING',
    FOREIGN KEY (employee_id) REFERENCES employees(employee_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_idle_logs_employee ON idle_logs(employee_id, upload_status);
