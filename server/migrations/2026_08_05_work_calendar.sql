-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — weekly offs and holidays
--
--  The scheduler had no idea some days are not working days. A Sunday looked
--  exactly like a Tuesday, so anyone who left the app running over a weekend
--  was captured through it, and every non-working day counted as an absence
--  once attendance reporting starts reading these tables.
--
--  TWO PIECES
--
--    employee_configs.weekly_offs   Which weekdays are off, as ISO weekday
--                                   numbers (1 = Monday ... 7 = Sunday) in a
--                                   comma-separated string: '7' for Sunday,
--                                   '6,7' for a two-day weekend, '' for none.
--                                   Per employee, falling back to the global
--                                   row exactly like every other config
--                                   field, so a night-shift team can have a
--                                   different day off from everyone else.
--
--    holidays                       Specific dates, company-wide. Kept apart
--                                   from employee_configs because a holiday
--                                   is a property of the calendar, not of a
--                                   person, and because the admin needs to
--                                   list and remove them individually.
--
--  ISO numbering rather than 0-6 because Python's date.isoweekday() and
--  Postgres's EXTRACT(ISODOW) already agree on it. The client and the server
--  both have to read this, and picking the numbering they share removes an
--  off-by-one that would only ever show up on one day of the week.
--
--  Defaults are deliberately empty. Existing installations keep behaving
--  exactly as they do today until an admin sets something.
--
--  Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE employee_configs
    ADD COLUMN IF NOT EXISTS weekly_offs VARCHAR(20) NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS holidays (
    id           SERIAL PRIMARY KEY,
    holiday_date DATE         NOT NULL UNIQUE,
    name         VARCHAR(120) NOT NULL,
    created_by   VARCHAR(50),
    created_at   TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

-- Every read is "the holidays between two dates", for the window the client
-- is told about and for a report's date range.
CREATE INDEX IF NOT EXISTS holidays_date_idx ON holidays (holiday_date);

COMMIT;
