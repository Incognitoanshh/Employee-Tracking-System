-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — late arrival grace period
--
--  Shift times have been stored for a while and login times have always been
--  recorded, but nothing ever compared the two. "Came in 40 minutes late" was
--  sitting in the database the whole time and was never once shown.
--
--  late_grace_minutes is how much lateness is not worth flagging. Without it
--  the feature is unusable: a shift starting at 09:00 would mark 09:00:04 as
--  late, every day, for everyone, and the column would be ignored within a
--  week.
--
--  Per employee with the usual fallback to the global row, because a field
--  team and an office team rarely deserve the same allowance.
--
--  Nothing is stored per attendance record. Late is computed when the page is
--  read, so correcting a wrongly-set shift fixes the history along with it
--  rather than leaving a trail of rows stamped with a rule that has since
--  changed.
--
--  Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE employee_configs
    ADD COLUMN IF NOT EXISTS late_grace_minutes INTEGER NOT NULL DEFAULT 10;

COMMIT;
