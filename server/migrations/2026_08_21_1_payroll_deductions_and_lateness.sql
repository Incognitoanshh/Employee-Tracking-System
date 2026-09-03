-- Payroll, brought up to the workflow the owner asked for: lateness on the
-- payslip, deductions as things in their own right, a remark on a salary, and
-- a department to group a report by.
--
-- EVERY COLUMN HERE IS ADDITIVE AND DEFAULTS TO THE VALUE THAT CHANGES
-- NOTHING. A payslip already finalised must print the same figures tomorrow as
-- it did today, so the new deduction columns default to 0 and the new policy
-- defaults to "off". Running this migration does not move a single rupee on
-- any existing run; only configuring the policy afterwards can, and only for
-- runs generated after that.
--
-- Money is NUMERIC, never a float, for the reason the first payroll migration
-- gives: 0.1 + 0.2 is not 0.3 in binary floating point, and this is somebody's
-- salary.

-- ── a remark on a salary ───────────────────────────────────────────────
--
-- "Why is this person on 25,000 from March?" is a question somebody asks a
-- year later, when whoever set it has left. The salary history already
-- records who and when; this records why.
ALTER TABLE employee_salaries
    ADD COLUMN IF NOT EXISTS remarks TEXT;

-- ── grouping a payroll report by department ────────────────────────────
--
-- The COLUMN already exists — 2026_08_12_1_employee_profile_fields added it,
-- and the admin panel has been setting it since. What is new is that payroll
-- now groups by it, which makes it something that gets read across every
-- employee at once rather than one row at a time.
--
-- Partial, because most rows are NULL: somebody who has not been given a
-- department is not "in the NULL department", and indexing thousands of NULLs
-- to find the handful that are set is work for nothing. The report falls back
-- to the designation and then to "Unassigned" for those.
CREATE INDEX IF NOT EXISTS employees_department
    ON employees (department) WHERE department IS NOT NULL;

-- ── lateness, frozen into the line like everything else ────────────────
--
-- Read from attendance when the run is generated and then never recomputed,
-- for the same reason as the rest of the line: a shift corrected in September
-- must not change what August's payslip says about August.
--
-- late_deduction is SEPARATE from late_minutes on purpose. Recording that
-- somebody was 340 minutes late over a month is a fact; deciding that it costs
-- them money is a policy, and a company that does not have that policy still
-- wants the fact on the payslip.
ALTER TABLE payroll_lines
    ADD COLUMN IF NOT EXISTS late_days     NUMERIC(5,1)  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS late_minutes  INTEGER       NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS late_deduction NUMERIC(12,2) NOT NULL DEFAULT 0,
    -- The sum of the rows in payroll_deductions for this line, frozen at
    -- generation the way the other components are. Kept on the line so a
    -- payslip can be printed without re-adding the parts, and checked against
    -- them by the tests.
    ADD COLUMN IF NOT EXISTS other_deductions NUMERIC(12,2) NOT NULL DEFAULT 0;

-- Postgres has no ADD CONSTRAINT IF NOT EXISTS, and a migration that cannot
-- be run twice is a migration that breaks the first time anything reruns it.
DO $$
BEGIN
    ALTER TABLE payroll_lines
        ADD CONSTRAINT payroll_late_not_negative
            CHECK (late_days >= 0 AND late_minutes >= 0 AND late_deduction >= 0);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- ── deductions somebody entered ────────────────────────────────────────
--
-- WHY THIS IS NOT payroll_adjustments WITH A MINUS SIGN. An adjustment is a
-- decision about one person in one month — a bonus, a fine, an advance
-- recovered. A deduction is a line every payslip carries because the company
-- or the law says so: provident fund, professional tax, ESI. They are printed
-- in different places, they are reported on differently, and a year-end
-- statement has to total them apart. Storing them in one table with a sign
-- would make "how much PF did we deduct" a question about the reason text.
--
-- AMOUNTS ARE POSITIVE HERE, unlike adjustments. A deduction only ever takes
-- money away, so the sign is in the table's meaning rather than in each row —
-- and a negative deduction, which reads as "we deducted minus five hundred",
-- is rejected rather than quietly added to somebody's pay.
--
-- PF, ESI, PT and TAX are PLACEHOLDERS, exactly as the brief asks: the table
-- carries them and the payslip prints them, but nothing computes them. They
-- are statutory, they differ by company and by year, and a wrong statutory
-- deduction is a legal problem rather than a bug. Somebody who knows the
-- company's obligations enters the figure.
CREATE TABLE IF NOT EXISTS payroll_deductions (
    id          BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
    employee_id VARCHAR(50) NOT NULL
                REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE CASCADE,
    kind        VARCHAR(24) NOT NULL,
    amount      NUMERIC(12,2) NOT NULL,
    reason      TEXT NOT NULL,
    created_by  VARCHAR(50) REFERENCES employees(employee_id)
                ON UPDATE CASCADE ON DELETE SET NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),

    CONSTRAINT deduction_kind_known CHECK (kind IN
        ('MANUAL', 'PROFESSIONAL_TAX', 'PF', 'ESI', 'TAX', 'OTHER')),
    CONSTRAINT deduction_has_reason CHECK (length(btrim(reason)) > 0),
    CONSTRAINT deduction_is_positive CHECK (amount > 0)
);

CREATE INDEX IF NOT EXISTS payroll_deductions_line
    ON payroll_deductions (run_id, employee_id);

-- ── the late policy, off until somebody turns it on ────────────────────
--
-- Stored as settings rather than code so that turning it on does not need a
-- release, and so that what it was when a run was generated is visible.
--
-- 'late_deduction_mode':
--    'NONE'      lateness is recorded and costs nothing.   ← the default
--    'PER_DAY'   every `late_grace_days` late days costs one day's pay.
--    'PRO_RATA'  the minutes are charged at the hourly rate.
--
-- NONE is the default deliberately. Deducting pay for lateness is a policy
-- decision with an employment contract behind it, and a migration is not where
-- a company decides to start doing it.
INSERT INTO app_settings (key, value) VALUES
    ('late_deduction_mode', 'NONE'),
    ('late_deduction_free_days', '0'),
    ('payroll_hours_per_day', '8')
ON CONFLICT (key) DO NOTHING;
