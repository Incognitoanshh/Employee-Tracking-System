-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — Production hardening migration
--  Run: psql -U <user> -d ets_db -f migrations/2026_08_02_production_hardening.sql
--
--  Idempotent — safely re-runnable.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ───────────────────────────────────────────────────────────────────────────
-- 1. Duplicate GLOBAL config rows
--
-- BUG: ets.sql aur add_admin_config.sql dono ye karte hain:
--
--     INSERT INTO employee_configs (employee_id, ...) VALUES (NULL, ...)
--     ON CONFLICT (employee_id) DO NOTHING;
--
-- Postgres me ek UNIQUE constraint NULLs ko ek doosre se DISTINCT maanta hai,
-- is liye NULL employee_id kabhi conflict karta hi nahi — `DO NOTHING` kabhi
-- trigger nahi hota. Nateeja: har baar schema/migration chalane pe ek AUR
-- global-default row ban jaati hai.
--
-- Iska asar: config.controller `ORDER BY updated_at DESC LIMIT 1` se ek row
-- uthata hai, jabki admin.controller `UPDATE ... WHERE employee_id IS NULL`
-- se SAARI rows update karta hai — yaani admin ka save kis row pe dikhega ye
-- non-deterministic ho jaata hai.
-- ───────────────────────────────────────────────────────────────────────────

-- Sabse recent global row rakho, baaki duplicates hata do.
DELETE FROM employee_configs
WHERE employee_id IS NULL
  AND id NOT IN (
      SELECT id FROM employee_configs
      WHERE employee_id IS NULL
      ORDER BY updated_at DESC NULLS LAST, id DESC
      LIMIT 1
  );

-- Aage se sirf EK hi global row ban sake — partial unique index NULL pe bhi
-- kaam karta hai (normal UNIQUE constraint ke ulat).
CREATE UNIQUE INDEX IF NOT EXISTS employee_configs_single_global
    ON employee_configs ((employee_id IS NULL))
    WHERE employee_id IS NULL;

-- Agar koi global row hi nahi hai to ek bana do.
--
-- The per-day screenshot column is deliberately NOT named here, and its
-- default is used instead.
--
-- BUG this fixes: it used to name `screenshot_count`, which was correct when
-- this migration was written and became wrong when 2026_08_04 renamed that
-- column to `screenshots_per_day`. Old databases still had the old name when
-- this ran (it runs first), so upgrades were fine — but a FRESH install gets
-- the current schema from ets.sql, where the old name does not exist, and
-- this statement failed with:
--
--     ERROR: column "screenshot_count" of relation "employee_configs"
--            does not exist
--
-- which stopped the whole migration and left the database half-built. Nobody
-- had hit it because nobody had set one up from scratch since the rename —
-- and the people who will are whoever takes this over.
--
-- Naming neither column works both ways: on an old database the old column
-- takes its default, and on a fresh one this is a no-op anyway, because
-- ets.sql has already inserted the global row.
INSERT INTO employee_configs
    (employee_id, screenshot_min_minutes, screenshot_max_minutes,
     upload_interval_minutes, idle_threshold_seconds,
     force_logout, verbose_logging)
SELECT NULL, 3, 10, 60, 60, false, false
WHERE NOT EXISTS (
    SELECT 1 FROM employee_configs WHERE employee_id IS NULL
);


-- ───────────────────────────────────────────────────────────────────────────
-- 2. Orphan config rows (aise employee_id ke liye jo employees me hai hi nahi)
--
-- Production (65.21.212.85) pe ye 3 junk rows mile:
--     'global'       -> deploy.sh `{"employee_id":"global"}` bhejta hai.
--                       saveConfig sirf `null` ko global maanta tha, string
--                       "global" else-branch me chala jaata tha aur ek asli
--                       employee ki tarah row ban jaati thi. Isi wajah se
--                       admin ke "Global Default" saves ASAL global row (NULL)
--                       pe kabhi pahunche hi nahi — wo is dummy row me jaate
--                       rahe (isi liye 'global'.min=5 tha jabki NULL.min=2).
--     'employee'     -> testing/docs se
--     'YOUR_EMP_ID'  -> docs placeholder se
--
-- Ye rows kabhi kisi employee se match nahi hotin (config lookup hamesha
-- employee_id ya NULL pe hota hai), sirf confusion paida karti hain.
--
-- NOTE: NULL wali global row is delete se safe hai — `IS NOT NULL` guard
-- lagi hui hai.
-- ───────────────────────────────────────────────────────────────────────────
DELETE FROM employee_configs ec
WHERE ec.employee_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM employees e WHERE e.employee_id = ec.employee_id
  );


-- ───────────────────────────────────────────────────────────────────────────
-- 3. Indexes
--
-- Ye saare columns har dashboard poll pe filter/sort hote hain. Admin panel
-- ke Dashboard/Employees/Screenshots/Logs tabs HAR 5 SECOND pe refresh hote
-- hain, aur har employee client har 30 second pe /logs/all maarta hai. Bina
-- index ke ye sab sequential scan hain — jaise-jaise activity_logs lakhon
-- rows tak pahunchega, ye poore DB ko ghutne pe le aayega.
-- ───────────────────────────────────────────────────────────────────────────

-- /logs/all (employee ka apna feed) + admin logs filter
CREATE INDEX IF NOT EXISTS idx_activity_logs_employee_id_desc
    ON activity_logs (employee_id, id DESC);

-- recent-activity feed + charts (ORDER BY id DESC / created_at range)
CREATE INDEX IF NOT EXISTS idx_activity_logs_created_at
    ON activity_logs (created_at DESC);

-- "kaun online hai" — har admin dashboard refresh pe chalta hai
CREATE INDEX IF NOT EXISTS idx_attendance_open_sessions
    ON attendance (employee_id, login_time DESC)
    WHERE logout_time IS NULL;

-- attendance history listing + pagination
CREATE INDEX IF NOT EXISTS idx_attendance_employee_id_desc
    ON attendance (employee_id, id DESC);

-- screenshots listing (admin filter + employee ka apna view)
CREATE INDEX IF NOT EXISTS idx_screenshots_employee_created
    ON screenshots (employee_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_screenshots_created_at
    ON screenshots (created_at DESC);

COMMIT;

-- Planner ko naye indexes ke baare me batao.
ANALYZE activity_logs;
ANALYZE attendance;
ANALYZE screenshots;
ANALYZE employee_configs;
