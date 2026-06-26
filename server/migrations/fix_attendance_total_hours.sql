-- Migration: Fix corrupted attendance total_hours
-- Runs after attendance.controller.js is updated

-- Fix records where logout_time exists but total_hours is NULL
-- and logout_time >= login_time (valid data)
UPDATE attendance
SET total_hours = logout_time - login_time
WHERE total_hours IS NULL
  AND logout_time IS NOT NULL
  AND logout_time >= login_time;

-- Log corrupted records for manual review
-- (logout_time < login_time - should never happen after fix)
SELECT
    id,
    employee_id,
    login_time,
    logout_time,
    'CORRUPTED: logout < login' as issue
FROM attendance
WHERE logout_time IS NOT NULL
  AND logout_time < login_time
ORDER BY id DESC;