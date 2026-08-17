-- Leave: asked for, decided on, and visible in attendance afterwards.
--
-- WHAT THIS FIXES ABOUT ATTENDANCE. Until now a day with no login was
-- "Absent", and that was the only thing it could be — approved leave, a
-- funeral and somebody who simply did not turn up all read the same. That is
-- the number a timesheet is built from, so it is also the number somebody is
-- paid on.
--
-- THE DAY IS A DATE, NOT A TIMESTAMP. Leave is granted in days, in the
-- office's own calendar; storing an instant would drag timezones into a thing
-- that has none. Everything else in this schema that means "a day" is a DATE
-- for the same reason.
--
-- HALF DAYS ARE A COUNT, NOT A FLAG. total_days is NUMERIC(4,1), so half a
-- day is 0.5 and two and a half days is 2.5. A boolean would have forced
-- every reader to know the rule, and payroll is going to read this.
--
-- WHAT IS DELIBERATELY NOT HERE
--
--   * No yearly quota. The owner's decision for this release: anybody may
--     ask for any amount and an administrator decides. A quota is a policy
--     that differs per company and per year, and inventing one would mean
--     enforcing a rule nobody agreed to.
--
--   * No approval chain. Any admin may approve, which is what was asked for.
--     A chain needs a reporting line that is only just being filled in.
--
--   * total_days is STORED, not computed on read. It is worked out when the
--     request is made, from the weekly offs and holidays in force THEN.
--     Recomputing it later would silently rewrite an approved request when
--     somebody adds a holiday, and an approved request is a promise.

CREATE TABLE IF NOT EXISTS leave_requests (
    id           BIGSERIAL PRIMARY KEY,
    employee_id  VARCHAR(50)  NOT NULL
                 REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE CASCADE,
    leave_type   VARCHAR(20)  NOT NULL,
    reason       TEXT         NOT NULL,
    start_date   DATE         NOT NULL,
    end_date     DATE         NOT NULL,
    -- 0.5 for half a day. Only ever half on a single-day request — half of a
    -- Tuesday inside a week off is not a thing anybody means.
    total_days   NUMERIC(4,1) NOT NULL,
    half_day     BOOLEAN      NOT NULL DEFAULT FALSE,
    status       VARCHAR(12)  NOT NULL DEFAULT 'PENDING',
    -- Who decided, and what they said. The remark is shown to the employee:
    -- a rejection with no reason is the thing that gets asked about in
    -- person, which is the conversation this is meant to save.
    approved_by  VARCHAR(50)  REFERENCES employees(employee_id)
                 ON UPDATE CASCADE ON DELETE SET NULL,
    approved_at  TIMESTAMP,
    remarks      TEXT,
    created_at   TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at   TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),

    CONSTRAINT leave_dates_in_order CHECK (end_date >= start_date),
    CONSTRAINT leave_days_positive  CHECK (total_days > 0),
    CONSTRAINT leave_status_known   CHECK (status IN
        ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED', 'REVOKED')),
    CONSTRAINT leave_type_known     CHECK (leave_type IN
        ('CASUAL', 'SICK', 'UNPAID')),
    -- Half a day is half of ONE day.
    CONSTRAINT leave_half_is_one_day CHECK (
        NOT half_day OR (start_date = end_date AND total_days = 0.5))
);

-- Attendance asks this on every row it draws: "was this person on approved
-- leave that day?" It is the busiest read in the feature.
CREATE INDEX IF NOT EXISTS leave_requests_employee_dates
    ON leave_requests (employee_id, start_date, end_date)
    WHERE status = 'APPROVED';

-- The admin's list is "what is waiting for me", newest first.
CREATE INDEX IF NOT EXISTS leave_requests_pending
    ON leave_requests (status, created_at DESC);
