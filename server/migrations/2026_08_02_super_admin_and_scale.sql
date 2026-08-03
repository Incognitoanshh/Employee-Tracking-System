-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — Super admin role + scale indexes
--  Run: psql ... -f migrations/2026_08_02_super_admin_and_scale.sql
--  Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ───────────────────────────────────────────────────────────────────────────
-- 1. Super admin
--
-- EMP001 company ka super admin hai — "god" role. Wo kisi ka bhi role badal
-- sakta hai, lekin use koi doosra demote/delete/modify nahi kar sakta.
-- ───────────────────────────────────────────────────────────────────────────
UPDATE employees SET role = 'super_admin' WHERE employee_id = 'EMP001';

-- SUPERSEDED — pehle yahan ek unique index tha jo sirf EK super_admin
-- allow karta tha:
--
--     CREATE UNIQUE INDEX employees_single_super_admin ...
--
-- Requirement baad me badli: ek super_admin doosra super_admin bana sakta
-- hai (dekho 2026_08_02_multi_super_admin.sql).
--
-- BUG: wo index yahan banta tha aur multi_super_admin.sql me drop hota tha.
-- Migrations agar dobara chalayi jayen (deploy scripts aksar `for f in
-- migrations/*.sql` karte hain) to alphabetical order me "multi_..." PEHLE
-- chalta hai aur "super_and_scale" BAAD me — yaani index wapas ban jaata
-- tha aur multiple super admins chup-chaap block ho jaate the.
-- Ab ye migration order-independent hai: index yahan banta hi nahi, aur
-- neeche defensive drop bhi hai.
DROP INDEX IF EXISTS employees_single_super_admin;

-- ───────────────────────────────────────────────────────────────────────────
-- 2. Scale indexes (1000–10,000 employees)
--
-- /admin/employees har employee ka "last activity" aur "last logout" nikalta
-- hai. Purani query MAX() use karti thi jo in indexes ko use NAHI kar paati.
-- 1000 employees + 20 lakh activity_logs pe measure kiya gaya:
--
--     purani query : 117 second   (aur ye har 5 second chalti thi)
--     nayi query   :  14 ms
--
-- Nayi query "ORDER BY created_at DESC LIMIT 1" use karti hai, jo in indexes
-- se seedha ek row uthata hai.
-- ───────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_activity_logs_emp_created
    ON activity_logs (employee_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_attendance_emp_logout
    ON attendance (employee_id, logout_time DESC)
    WHERE logout_time IS NOT NULL;

-- Employees list ka search (employee_id / username / role pe ILIKE)
CREATE INDEX IF NOT EXISTS idx_employees_employee_id
    ON employees (employee_id ASC);

COMMIT;

ANALYZE employees;
ANALYZE activity_logs;
ANALYZE attendance;
