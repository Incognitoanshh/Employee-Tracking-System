-- Payroll: what somebody is paid, and the arithmetic that got there.
--
-- THE RULE THAT SHAPES EVERY TABLE HERE. A payslip is a statement about a
-- month that has already happened. Once it is finalised, the numbers on it
-- must never change because something else changed later — a holiday added
-- in September must not alter August's pay, and neither must a salary rise,
-- a corrected shift, or leave approved after the fact.
--
-- So the components are FROZEN INTO payroll_lines when the run is generated:
-- the working days, the present days, the leave, the absence, the per-day
-- rate. Nothing is recomputed on read. A finalised line is a record, not a
-- view.
--
-- Money is NUMERIC, never a float. 0.1 + 0.2 is not 0.3 in binary floating
-- point, and this is somebody's salary.

-- ── what somebody is paid ──────────────────────────────────────────────
--
-- A HISTORY, not a column on employees. A rise in June must not rewrite May's
-- payslip, so each row carries the date it takes effect from and a payroll
-- run picks the one in force for its month. Nothing is ever updated in place.
CREATE TABLE IF NOT EXISTS employee_salaries (
    id              BIGSERIAL PRIMARY KEY,
    employee_id     VARCHAR(50) NOT NULL
                    REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE CASCADE,
    gross_monthly   NUMERIC(12,2) NOT NULL,
    -- Overtime is paid at a rate somebody sets, not derived from the salary.
    -- Companies differ, and guessing would be inventing a policy.
    overtime_hourly NUMERIC(10,2) NOT NULL DEFAULT 0,
    effective_from  DATE NOT NULL,
    created_by      VARCHAR(50) REFERENCES employees(employee_id)
                    ON UPDATE CASCADE ON DELETE SET NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),

    CONSTRAINT salary_not_negative CHECK (gross_monthly >= 0),
    CONSTRAINT overtime_not_negative CHECK (overtime_hourly >= 0),
    -- One salary per person per effective date. Two rows for the same day
    -- would make "which one applies" a coin toss.
    CONSTRAINT salary_one_per_day UNIQUE (employee_id, effective_from)
);

CREATE INDEX IF NOT EXISTS employee_salaries_current
    ON employee_salaries (employee_id, effective_from DESC);

-- ── a month's payroll ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payroll_runs (
    id            BIGSERIAL PRIMARY KEY,
    -- The FIRST of the month it covers. A date rather than "2026-08" so the
    -- database can compare and order it without parsing strings.
    month         DATE NOT NULL UNIQUE,
    status        VARCHAR(12) NOT NULL DEFAULT 'DRAFT',
    working_days  NUMERIC(5,1) NOT NULL,
    generated_by  VARCHAR(50) REFERENCES employees(employee_id)
                  ON UPDATE CASCADE ON DELETE SET NULL,
    generated_at  TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    finalized_by  VARCHAR(50) REFERENCES employees(employee_id)
                  ON UPDATE CASCADE ON DELETE SET NULL,
    finalized_at  TIMESTAMP,

    CONSTRAINT payroll_status_known CHECK (status IN ('DRAFT', 'FINALIZED'))
);

-- ── one person's month ─────────────────────────────────────────────────
--
-- Every figure the payslip prints, kept as it was when the run was made. The
-- deductions are stored as amounts rather than recomputed from the days,
-- because the per-day rate depends on the working days of THAT month and
-- nobody should have to reconstruct it years later.
CREATE TABLE IF NOT EXISTS payroll_lines (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
    employee_id         VARCHAR(50) NOT NULL
                        REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE CASCADE,

    gross_monthly       NUMERIC(12,2) NOT NULL,
    working_days        NUMERIC(5,1)  NOT NULL,
    present_days        NUMERIC(5,1)  NOT NULL DEFAULT 0,
    paid_leave_days     NUMERIC(5,1)  NOT NULL DEFAULT 0,
    unpaid_leave_days   NUMERIC(5,1)  NOT NULL DEFAULT 0,
    absent_days         NUMERIC(5,1)  NOT NULL DEFAULT 0,

    per_day             NUMERIC(12,2) NOT NULL DEFAULT 0,
    absent_deduction    NUMERIC(12,2) NOT NULL DEFAULT 0,
    unpaid_deduction    NUMERIC(12,2) NOT NULL DEFAULT 0,

    overtime_hours      NUMERIC(6,2)  NOT NULL DEFAULT 0,
    overtime_rate       NUMERIC(10,2) NOT NULL DEFAULT 0,
    overtime_amount     NUMERIC(12,2) NOT NULL DEFAULT 0,

    -- Gross, less the deductions, plus overtime. Adjustments are NOT in here:
    -- they are added after finalisation as well as before, so the payslip
    -- shows this figure and then the adjustments that moved it.
    net_before_adjustments NUMERIC(12,2) NOT NULL DEFAULT 0,

    CONSTRAINT payroll_one_line_per_person UNIQUE (run_id, employee_id)
);

-- ── the things somebody decided by hand ────────────────────────────────
--
-- Adjustments may be added AFTER a run is finalised — that is how a finalised
-- payroll is corrected, rather than by editing it. The reason is required for
-- exactly that: an amount with no explanation is unanswerable a year later.
CREATE TABLE IF NOT EXISTS payroll_adjustments (
    id          BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
    employee_id VARCHAR(50) NOT NULL
                REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE CASCADE,
    kind        VARCHAR(20) NOT NULL,
    -- SIGNED. A fine is negative, a bonus positive, and the sign lives in the
    -- number rather than in the reader's head.
    amount      NUMERIC(12,2) NOT NULL,
    reason      TEXT NOT NULL,
    created_by  VARCHAR(50) REFERENCES employees(employee_id)
                ON UPDATE CASCADE ON DELETE SET NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),

    CONSTRAINT adjustment_kind_known CHECK (kind IN
        ('BONUS', 'INCENTIVE', 'REIMBURSEMENT', 'ADVANCE', 'FINE', 'OTHER')),
    CONSTRAINT adjustment_has_reason CHECK (length(btrim(reason)) > 0),
    CONSTRAINT adjustment_not_zero CHECK (amount <> 0)
);

CREATE INDEX IF NOT EXISTS payroll_adjustments_line
    ON payroll_adjustments (run_id, employee_id);

-- Shown on every payslip. Kept as settings rather than hardcoded, because a
-- payslip with the wrong company on it is not a payslip.
INSERT INTO app_settings (key, value) VALUES
    ('company_name',    'Amaze Internet'),
    ('company_address', ''),
    ('payroll_currency', '₹')
ON CONFLICT (key) DO NOTHING;
