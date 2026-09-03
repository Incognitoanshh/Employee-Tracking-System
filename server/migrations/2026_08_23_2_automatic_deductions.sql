-- Which deductions the system worked out, and which a person typed.
--
-- WHY THIS COLUMN HAS TO EXIST. Provident fund, ESI and professional tax are
-- now computed when a month is generated, from the rates and the per-employee
-- switches. Generating a month again — which happens whenever an attendance
-- row is corrected or a late leave request is approved — has to rebuild them,
-- exactly as it rebuilds the lines.
--
-- But the same table also holds deductions somebody entered BY HAND, with a
-- reason, against one person. Those must survive a rebuild untouched; that is
-- already the promise made when regenerating carries them forward.
--
-- Without a way to tell the two apart there are only two outcomes, and both
-- are wrong: rebuild everything and a hand-entered deduction disappears, or
-- rebuild nothing and every regeneration adds another provident fund row on
-- top of the last. The second is the dangerous one — it is silent, and it
-- deducts twice from somebody's pay.
--
-- FALSE by default, so every deduction that exists today is treated as what
-- it is: entered by a person, and never rebuilt.
ALTER TABLE payroll_deductions
    ADD COLUMN IF NOT EXISTS automatic BOOLEAN NOT NULL DEFAULT FALSE;

-- The rebuild deletes by (run, automatic) on every generate, so it is worth
-- an index of its own.
CREATE INDEX IF NOT EXISTS payroll_deductions_automatic
    ON payroll_deductions (run_id) WHERE automatic;
