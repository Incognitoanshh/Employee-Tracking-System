-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — Data retention purge  (MANUAL / CRON)
--
--  PROBLEM: `activity_logs` aur `screenshots` me kabhi kuch delete nahi hota.
--  1000 employees pe:
--      activity_logs : ~1.1 crore rows/din  ->  ~275 crore rows/saal (~300 GB)
--      screenshots   : ~30 lakh files/saal
--
--  Iska asar sirf disk pe nahi — har COUNT(*), har dashboard query, har
--  index dhire hota jaayega. 6-8 mahine me admin panel bilkul unusable.
--
--  Ye script JAAN-BOOJH KAR migration nahi hai aur apne aap nahi chalti —
--  data delete karna business/compliance ka decision hai, code ka nahi.
--  Retention period apni policy ke hisaab se badlo, phir cron pe lagao:
--
--      # har raat 3 baje
--      0 3 * * * psql -d ets_db -f /path/to/retention_purge.sql >> /var/log/ets-purge.log 2>&1
--
--  ⚠️  screenshots ki DB rows delete karne se uploads/ ki FILES nahi hatengi.
--      Neeche wali query un orphan files ki list deti hai — unhe alag se
--      delete karna hoga (script neeche comment me hai).
-- ═══════════════════════════════════════════════════════════════════════════

\set log_retention_days 90
\set screenshot_retention_days 180

BEGIN;

-- 1. Purane activity logs
DELETE FROM activity_logs
WHERE created_at < NOW() - (:'log_retention_days' || ' days')::interval;

-- 2. Purane screenshots (DB rows)
DELETE FROM screenshots
WHERE created_at < NOW() - (:'screenshot_retention_days' || ' days')::interval;

-- 3. Purane attendance records — payroll ke liye zyada der rakho
DELETE FROM attendance
WHERE login_time < NOW() - INTERVAL '2 years';

-- 4. Dangling sessions (jo employees delete ho chuke)
DELETE FROM active_sessions a
WHERE NOT EXISTS (SELECT 1 FROM employees e WHERE e.employee_id = a.employee_id);

COMMIT;

VACUUM ANALYZE activity_logs;
VACUUM ANALYZE screenshots;

SELECT 'activity_logs' AS tbl, COUNT(*) FROM activity_logs
UNION ALL SELECT 'screenshots', COUNT(*) FROM screenshots
UNION ALL SELECT 'attendance',  COUNT(*) FROM attendance;

-- ─────────────────────────────────────────────────────────────────────────
--  ORPHAN FILES (DB me nahi, disk pe padi hain) — shell se hatao:
--
--    psql -d ets_db -At -c "SELECT file_name FROM screenshots" > /tmp/keep.txt
--    cd server/uploads/screenshots
--    ls | grep -vxFf /tmp/keep.txt | xargs -r rm --
-- ─────────────────────────────────────────────────────────────────────────
