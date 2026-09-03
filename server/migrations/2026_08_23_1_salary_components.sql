-- Salary as a STRUCTURE, not a single number.
--
-- Until now a salary was one figure: gross_monthly. A real payslip is that
-- figure broken into parts — Basic, DA, house rent, conveyance — because the
-- parts are what everything else is computed from. Provident fund is a
-- percentage of Basic plus DA, not of the whole salary; house rent allowance
-- has its own tax treatment; a rise is usually given on CTC and has to be
-- redistributed across all of them.
--
-- WHAT IS AND IS NOT DECIDED HERE
--
-- The SPLIT is a company policy: how much of the cost to company is Basic,
-- and what the allowances are as a share of it. That is configurable, seeded
-- with the arrangement in the reference (Basic 50% of CTC, DA 20% of Basic,
-- HRA 50% of Basic, conveyance 15% of Basic, and whatever is left as a fixed
-- allowance so the parts always add back to the whole).
--
-- The STATUTORY rates — provident fund, ESI, professional tax — are carried
-- as settings with the usual Indian defaults, and they are OFF for everybody
-- until somebody turns them on per employee. This is the same line the rest
-- of payroll already draws: a wrong statutory deduction is a legal problem
-- rather than a bug, so the system will apply a rate somebody set and will
-- never invent one.
--
-- EVERY COLUMN IS ADDITIVE AND DEFAULTS TO CHANGING NOTHING. An employee with
-- no components keeps behaving exactly as before, on gross_monthly alone.

-- ── the cost to company a salary was set from ──────────────────────────
--
-- Nullable on purpose: salaries set before this migration were entered as a
-- monthly gross with no CTC behind them, and inventing one by multiplying by
-- twelve would be putting a number nobody agreed into the record.
ALTER TABLE employee_salaries
    ADD COLUMN IF NOT EXISTS ctc_annual NUMERIC(14,2),
    -- Statutory, per person, and off unless somebody says otherwise.
    ADD COLUMN IF NOT EXISTS epf_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS esi_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS pt_enabled  BOOLEAN NOT NULL DEFAULT FALSE;

DO $$
BEGIN
    ALTER TABLE employee_salaries
        ADD CONSTRAINT salary_ctc_not_negative CHECK (ctc_annual IS NULL OR ctc_annual >= 0);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- ── the parts a salary is made of ──────────────────────────────────────
--
-- One row per component per salary VERSION. Tied to employee_salaries rather
-- than to the employee, because a rise rewrites the split and the old split
-- must survive with the salary it belonged to — the same rule that makes
-- payroll_lines frozen.
--
-- Both the RULE and the AMOUNT are stored. The rule is what somebody chose
-- ("50% of Basic"); the amount is what it came to when the salary was set.
-- Keeping only the rule would mean recomputing a two-year-old payslip from
-- today's policy; keeping only the amount would lose why it is that figure.
CREATE TABLE IF NOT EXISTS salary_components (
    id           BIGSERIAL PRIMARY KEY,
    salary_id    BIGINT NOT NULL REFERENCES employee_salaries(id) ON DELETE CASCADE,
    -- Display order, so a payslip lists Basic before the allowances rather
    -- than in whatever order the rows come back.
    position     INTEGER NOT NULL DEFAULT 0,
    name         VARCHAR(60) NOT NULL,
    -- PERCENT_CTC   value% of the monthly cost to company
    -- PERCENT_BASIC value% of Basic
    -- FIXED         value rupees a month, as entered
    -- BALANCE       whatever is left of the CTC after everything else
    rule         VARCHAR(16) NOT NULL,
    value        NUMERIC(10,3) NOT NULL DEFAULT 0,
    monthly      NUMERIC(12,2) NOT NULL DEFAULT 0,

    CONSTRAINT component_rule_known CHECK (rule IN
        ('PERCENT_CTC', 'PERCENT_BASIC', 'FIXED', 'BALANCE')),
    CONSTRAINT component_has_name CHECK (length(btrim(name)) > 0),
    -- A component may be zero — a company that pays no conveyance still
    -- wants the line on the payslip — but never negative. Money taken away
    -- is a deduction, and deductions have their own table.
    CONSTRAINT component_not_negative CHECK (monthly >= 0),
    CONSTRAINT component_one_name_per_salary UNIQUE (salary_id, name)
);

CREATE INDEX IF NOT EXISTS salary_components_salary
    ON salary_components (salary_id, position);

-- ── the company's default split ────────────────────────────────────────
--
-- The template a new salary starts from. Stored as settings so changing it
-- does not need a release, and so that what it was is visible.
--
-- It is a STARTING POINT, not a rule: the components written against a salary
-- are that salary's own, and editing the template later does not reach back
-- into salaries already set.
INSERT INTO app_settings (key, value) VALUES
    ('salary_template',
     '[{"name":"Basic","rule":"PERCENT_CTC","value":50},'
   || '{"name":"DA","rule":"PERCENT_BASIC","value":20},'
   || '{"name":"House Rent Allowance","rule":"PERCENT_BASIC","value":50},'
   || '{"name":"Conveyance Allowance","rule":"PERCENT_BASIC","value":15},'
   || '{"name":"Fixed Allowance","rule":"BALANCE","value":0}]'),
    -- Statutory rates, as they stand in India. Applied only to employees
    -- somebody has switched them on for.
    ('epf_rate',            '12'),      -- of Basic + DA
    ('epf_wage_ceiling',    '15000'),   -- the ceiling that rate applies below
    ('esi_rate',            '0.75'),    -- of gross, employee's share
    ('esi_wage_ceiling',    '21000'),
    ('professional_tax',    '200')      -- a flat monthly figure, state by state
ON CONFLICT (key) DO NOTHING;
