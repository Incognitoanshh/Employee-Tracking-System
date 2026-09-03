-- What a payslip's gross is made of, frozen onto the payslip.
--
-- A payslip shows one figure — the monthly gross — and a real one shows what
-- that figure is MADE OF: Basic, DA, house rent, conveyance, the balance. The
-- parts are what everything else is computed from; provident fund is a share
-- of Basic plus DA, not of the gross, so a payslip that hides them cannot be
-- checked by the person it belongs to.
--
-- ── WHY THE PARTS ARE COPIED AND NOT REFERENCED ────────────────────────
--
-- salary_components already holds a split, one row per component per salary
-- VERSION, and the obvious design is a foreign key from the line to the
-- salary it was built from. That is what this migration did in its first
-- draft, and it is wrong, for a reason worth writing down:
--
--     setSalary is INSERT ... ON CONFLICT (employee_id, effective_from)
--     DO UPDATE, and it then DELETEs the components of that row and writes
--     the new ones.
--
-- So correcting a salary on the SAME effective date keeps the same salary id
-- and silently rewrites its split. A payslip pointing at that id would carry
-- its own frozen gross and somebody else's parts — and the parts would stop
-- adding up to the total printed directly above them. On a finalised month.
-- That is precisely the thing payroll_lines is frozen to prevent.
--
-- A payslip's components are part of the payslip, so they are stored with it,
-- exactly as the gross, the deductions and the net already are. Nothing that
-- happens to a salary afterwards can reach them.
--
-- ── AND NOTHING EXISTING IS DISTURBED ──────────────────────────────────
--
-- No backfill. A line generated before this has no components and says so on
-- the payslip; it does not get a split computed from whatever the salary
-- looks like today. Once written, such a guess would be indistinguishable
-- from a figure that had actually been frozen at the time.
CREATE TABLE IF NOT EXISTS payroll_line_components (
    id       BIGSERIAL PRIMARY KEY,
    line_id  BIGINT NOT NULL REFERENCES payroll_lines(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    name     VARCHAR(60) NOT NULL,
    -- The RULE as well as the amount, the same pair salary_components keeps:
    -- the rule is what somebody chose ("50% of Basic"), the amount is what it
    -- came to. Keeping only the amount loses why it is that figure.
    rule     VARCHAR(16) NOT NULL,
    value    NUMERIC(10,3) NOT NULL DEFAULT 0,
    monthly  NUMERIC(12,2) NOT NULL DEFAULT 0,
    CONSTRAINT line_component_has_name CHECK (length(btrim(name)) > 0),
    CONSTRAINT line_component_not_negative CHECK (monthly >= 0),
    CONSTRAINT line_component_rule_known CHECK (
        rule IN ('PERCENT_CTC', 'PERCENT_BASIC', 'FIXED', 'BALANCE')),
    CONSTRAINT line_component_one_name UNIQUE (line_id, name)
);

-- ON DELETE CASCADE, deliberately. Regenerating a draft deletes and rebuilds
-- its lines, and a component row left behind would attach itself to nothing.
-- A finalised month is never regenerated, so nothing there is at risk.
CREATE INDEX IF NOT EXISTS payroll_line_components_line
    ON payroll_line_components (line_id, position);

-- ── which arrangement it came from, for provenance ─────────────────────
--
-- Kept as well, because "which salary version was this built on" is a real
-- question when somebody queries a payslip. It is NOT what the payslip reads
-- its parts from — see above. SET NULL rather than CASCADE: deleting a salary
-- must never take a payslip with it.
ALTER TABLE payroll_lines
    ADD COLUMN IF NOT EXISTS salary_id BIGINT
        REFERENCES employee_salaries(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS payroll_lines_salary
    ON payroll_lines (salary_id) WHERE salary_id IS NOT NULL;
