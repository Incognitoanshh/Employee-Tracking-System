-- My Profile — the columns a person's own account page needs.
--
-- Every one of these is NULLABLE and has no default, so this migration adds
-- nothing to any existing row and no query that runs today changes its
-- answer. An employee who was created before this ran simply has a profile
-- with blanks in it, which is the truth about them.
--
-- WHAT IS DELIBERATELY NOT HERE
--
--   * Nothing is moved out of `employees`. A profile is one row about one
--     person; a second table would mean a join on every screen that shows a
--     name, and two places to keep in step.
--
--   * `photo` holds a FILE NAME, not the image. Screenshots already work this
--     way — bytes on disk under UPLOAD_DIR, the row naming them — and putting
--     images in the database would put them in every backup and every dump.
--
--   * No `email` column: the product has never had one, and inventing a field
--     nobody fills is worse than a page that does not claim to know.
--     `username` is what people are identified by here.
--
-- `ip` on active_sessions is for the session list on that page: somebody
-- looking at "where am I signed in" needs more than a device id they have
-- never seen. It is written where the session is written and read nowhere
-- else, so an older row simply shows nothing.

ALTER TABLE employees ADD COLUMN IF NOT EXISTS phone             VARCHAR(32);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS department        VARCHAR(120);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS reporting_manager VARCHAR(50);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS joining_date      DATE;
ALTER TABLE employees ADD COLUMN IF NOT EXISTS employment_status VARCHAR(32);
ALTER TABLE employees ADD COLUMN IF NOT EXISTS photo             TEXT;

-- The manager is another employee. ON DELETE SET NULL rather than CASCADE:
-- somebody leaving must not delete the people who reported to them.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_employee_manager'
    ) THEN
        ALTER TABLE employees
            ADD CONSTRAINT fk_employee_manager
            FOREIGN KEY (reporting_manager) REFERENCES employees(employee_id)
            ON DELETE SET NULL;
    END IF;
END $$;

-- Only the values the product actually uses. Without this the column takes
-- any string, and a typo in a script becomes a status nothing recognises.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_employment_status'
    ) THEN
        ALTER TABLE employees
            ADD CONSTRAINT chk_employment_status
            CHECK (employment_status IS NULL OR employment_status IN
                   ('active', 'probation', 'notice_period', 'resigned', 'terminated'));
    END IF;
END $$;

ALTER TABLE active_sessions ADD COLUMN IF NOT EXISTS ip VARCHAR(45);

-- Session history is read newest-first for one person.
CREATE INDEX IF NOT EXISTS idx_active_sessions_employee
    ON active_sessions (employee_id, login_time DESC);
