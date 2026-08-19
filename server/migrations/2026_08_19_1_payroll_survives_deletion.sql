-- A FINALISED PAYROLL RUN MUST NOT CHANGE WHEN SOMEBODY LEAVES.
--
-- WHAT WAS HAPPENING, MEASURED. payroll_lines.employee_id and
-- payroll_adjustments.employee_id both carried ON DELETE CASCADE. Deleting an
-- employee therefore deleted their line out of a run that was already
-- FINALISED: in a scratch database the run's total went from 50,000.00 to 0
-- while the run itself still read "FINALIZED". Nothing warned anybody, and
-- nothing in the audit log said a number had moved.
--
-- That contradicts the rule the product states everywhere else — "a finalised
-- month stops moving; anything after it is an adjustment, on the record" —
-- and it is the kind of silent change a payroll record must never make.
--
-- WHY DROP THE CONSTRAINT RATHER THAN RESTRICT THE DELETE. The deletion
-- contract is already written down in test_employee_deletion.js and it is a
-- deliberate one: tracking data goes, and anything that is a RECORD stays,
-- attributed to a former employee rather than to nobody. Messages already
-- work exactly that way (sender_id SET NULL, sender_name kept). Refusing the
-- delete would break that contract; keeping the row honours it.
--
-- The id stays in the row as plain text — retired_employee_ids already holds
-- the name against that id, which is what the reader needs.
--
-- NOT NULL stays: a payroll line without an employee is not a record of
-- anything, and no code path produces one.

ALTER TABLE payroll_lines
    DROP CONSTRAINT IF EXISTS payroll_lines_employee_id_fkey;

ALTER TABLE payroll_adjustments
    DROP CONSTRAINT IF EXISTS payroll_adjustments_employee_id_fkey;

-- The index the foreign key was providing for free. Without it, "every line
-- for this person" — which is what a payslip history is — becomes a scan.
CREATE INDEX IF NOT EXISTS idx_payroll_lines_employee
    ON payroll_lines (employee_id);

CREATE INDEX IF NOT EXISTS idx_payroll_adjustments_employee
    ON payroll_adjustments (employee_id);

-- Salary history is the same kind of record: it is what a past run was
-- computed FROM, so an audit of that run needs it to still be there.
ALTER TABLE employee_salaries
    DROP CONSTRAINT IF EXISTS employee_salaries_employee_id_fkey;

CREATE INDEX IF NOT EXISTS idx_employee_salaries_employee
    ON employee_salaries (employee_id);
