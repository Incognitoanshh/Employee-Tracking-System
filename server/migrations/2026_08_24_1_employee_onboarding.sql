-- Everything asked for when somebody joins, and the account they may not have.
--
-- Until now an employee was six fields typed into a small dialog: id, name,
-- designation, username, password, role. A payroll needs more than that — the
-- date they joined decides their first month, the PAN goes on the tax filing,
-- and the bank details are how the money actually leaves the account.
--
-- ── THE PART THAT TOUCHES AUTHENTICATION ────────────────────────────────
--
-- username and password become NULLABLE, so that an employee can exist on the
-- payroll WITHOUT being able to sign in: a contractor, or anybody who is paid
-- but never runs the desktop client. Credentials can be issued later.
--
-- This cannot let anybody in who could not get in before, and the reason is
-- SQL rather than a check somebody has to remember. Login is:
--
--     SELECT * FROM employees WHERE LOWER(username) = LOWER($1)
--
-- LOWER(NULL) is NULL, and NULL = anything is NULL, never true. A row with no
-- username is unreachable by that query no matter what is typed at the login
-- box. The route also refuses an empty username before it queries at all.
--
-- The change is additive: every employee that exists today has a username, so
-- nothing about them changes. Both halves are held down by
-- server/tests/test_employee_onboarding.js — that a credential-less employee
-- cannot sign in, and that everybody else signs in exactly as before.
ALTER TABLE employees ALTER COLUMN username DROP NOT NULL;
ALTER TABLE employees ALTER COLUMN password DROP NOT NULL;

-- A partial unique index already exists on LOWER(username). NULLs do not
-- collide in a unique index, so any number of employees may have no username
-- while two of them still cannot share one.

-- ── who they are ────────────────────────────────────────────────────────
ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS gender        VARCHAR(24),
    ADD COLUMN IF NOT EXISTS work_location VARCHAR(120),
    ADD COLUMN IF NOT EXISTS date_of_birth DATE,
    ADD COLUMN IF NOT EXISTS pan           VARCHAR(10),
    ADD COLUMN IF NOT EXISTS address       TEXT;

-- A SHORT LIST, AND "PREFER NOT TO SAY" IS ON IT. A required field with two
-- options is a question somebody has to answer wrongly; NULL is also allowed,
-- because this is not needed to pay anybody.
ALTER TABLE employees DROP CONSTRAINT IF EXISTS chk_gender;
ALTER TABLE employees ADD CONSTRAINT chk_gender CHECK (
    gender IS NULL OR gender IN ('male', 'female', 'other', 'prefer_not_to_say'));

-- FIVE LETTERS, FOUR DIGITS, ONE LETTER — the shape the income tax department
-- issues and the shape a filing is rejected for not matching. Stored upper
-- case so two spellings of the same PAN cannot both exist.
ALTER TABLE employees DROP CONSTRAINT IF EXISTS chk_pan;
ALTER TABLE employees ADD CONSTRAINT chk_pan CHECK (
    pan IS NULL OR pan ~ '^[A-Z]{5}[0-9]{4}[A-Z]$');

-- ── how they are paid ───────────────────────────────────────────────────
--
-- The bank columns are nullable on purpose: they are meaningless for cash and
-- cheque, and an employee added before payday may not have handed them over
-- yet. What must never happen is a payment mode nobody handles, so the mode
-- itself is constrained.
ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS payment_mode        VARCHAR(24),
    ADD COLUMN IF NOT EXISTS bank_name           VARCHAR(120),
    ADD COLUMN IF NOT EXISTS bank_account_number VARCHAR(34),
    ADD COLUMN IF NOT EXISTS bank_ifsc           VARCHAR(11);

ALTER TABLE employees DROP CONSTRAINT IF EXISTS chk_payment_mode;
ALTER TABLE employees ADD CONSTRAINT chk_payment_mode CHECK (
    payment_mode IS NULL OR payment_mode IN ('bank_transfer', 'cheque', 'cash'));

-- FOUR LETTERS, A ZERO, SIX MORE — the Reserve Bank's format. A transfer to a
-- malformed IFSC does not bounce politely; it fails at the bank days later,
-- by which time payday has passed.
ALTER TABLE employees DROP CONSTRAINT IF EXISTS chk_bank_ifsc;
ALTER TABLE employees ADD CONSTRAINT chk_bank_ifsc CHECK (
    bank_ifsc IS NULL OR bank_ifsc ~ '^[A-Z]{4}0[A-Z0-9]{6}$');
