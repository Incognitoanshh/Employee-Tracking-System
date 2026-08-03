-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — Multiple super admins allowed
--
--  Pehli migration ne ek unique index lagaya tha jo sirf EK super_admin
--  allow karta tha. Requirement badli: ek super_admin doosra super_admin
--  bana sakta hai (sirf super_admin hi — admin nahi).
--
--  Baaki protection waisi ki waisi hai (application layer pe):
--    - super_admin ko koi delete / demote / modify nahi kar sakta
--    - admin sirf admin aur employee bana sakta hai
--    - admin doosre admin ko modify nahi kar sakta
--
--  Run: psql ... -f migrations/2026_08_02_multi_super_admin.sql
--  Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

DROP INDEX IF EXISTS employees_single_super_admin;

-- Safety net: kam se kam ek super_admin hamesha hona chahiye, warna role
-- management poori tarah lock ho jayega (koi promote/demote nahi kar payega).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM employees WHERE role = 'super_admin') THEN
        UPDATE employees SET role = 'super_admin' WHERE employee_id = 'EMP001';
        RAISE NOTICE 'No super_admin found — EMP001 promoted to super_admin.';
    END IF;
END$$;

COMMIT;

SELECT employee_id, username, role
FROM employees
WHERE role <> 'employee'
ORDER BY role DESC, employee_id;
