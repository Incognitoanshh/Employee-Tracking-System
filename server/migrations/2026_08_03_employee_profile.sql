-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — Employee profile fields
--
--  Naye employee panel ke header me employee ka asli naam aur designation
--  dikhta hai ("Amritanshu · Software Developer"). Ab tak `employees` table
--  me sirf employee_id / username / role the — koi human-readable naam hi
--  nahi tha, is liye UI me hamesha "EMP002" jaisa raw ID hi dikhana padta.
--
--  Dono columns NULLABLE hain — purane employees bina kisi change ke chalte
--  rahenge, UI un par username pe fall back kar leta hai.
--
--  Run: psql ... -f migrations/2026_08_03_employee_profile.sql
--  Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE employees ADD COLUMN IF NOT EXISTS full_name   VARCHAR(120);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS designation VARCHAR(120);

-- Jinke paas naam nahi hai unke liye username hi naam maan lo (better than
-- raw employee_id dikhana). Admin baad me panel se sahi naam bhar sakta hai.
UPDATE employees
SET full_name = INITCAP(username)
WHERE full_name IS NULL OR full_name = '';

UPDATE employees
SET designation = CASE
    WHEN role = 'super_admin' THEN 'Administrator'
    WHEN role = 'admin'       THEN 'Manager'
    ELSE 'Employee'
END
WHERE designation IS NULL OR designation = '';

COMMIT;

SELECT employee_id, username, full_name, designation, role
FROM employees
ORDER BY employee_id;
