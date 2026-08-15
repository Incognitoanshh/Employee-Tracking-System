-- Proving an email address belongs to the person who typed it.
--
-- The address column added an hour before this records what somebody typed.
-- That is worth having on its own — an admin needs a way to reach people —
-- but it is not proof of anything, and the moment anything is SENT to that
-- address on the strength of it (a password reset, a report, a notice) the
-- difference matters. A typo sends company mail to a stranger; a deliberate
-- wrong address sends it somewhere it was meant to go.
--
-- WHAT IS STORED, AND WHAT IS NOT
--
--   * The code is stored HASHED, never in plain text. It is short-lived and
--     low-value, but it is also a credential, and a database dump or a
--     backup on somebody's laptop should not contain a working one. The same
--     reasoning as `password` two columns over.
--
--   * ONE PENDING VERIFICATION PER PERSON — employee_id is the primary key,
--     so asking for a new code replaces the old one rather than leaving a
--     trail of codes that all still work. Somebody who requests three codes
--     because the first two were slow to arrive should find that exactly one
--     of them opens the door: the last.
--
--   * `email` is stored ALONGSIDE the code, because the address is what is
--     being proved. Without it somebody could request a code for an address
--     they control, change the address on their profile, and then verify the
--     new one with the old code.
--
--   * `attempts` exists so that guessing is bounded. Six digits is a million
--     possibilities, which is plenty against a person and nothing at all
--     against a loop.
--
--   * `sent_at` exists so that a resend can be throttled. Without it the
--     button is a way to send somebody a hundred emails.
--
-- `email_verified_at` on employees is the ANSWER, kept separately from the
-- pending attempt: the row below is deleted once it succeeds, and what
-- survives is the fact that the address was proved, and when. Changing the
-- address clears it — see profile.controller.js — because what was proved was
-- the old one.

ALTER TABLE employees ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS email_verifications (
    employee_id  VARCHAR(50) PRIMARY KEY
                 REFERENCES employees(employee_id) ON DELETE CASCADE,
    email        VARCHAR(255) NOT NULL,
    code_hash    TEXT         NOT NULL,
    expires_at   TIMESTAMP    NOT NULL,
    attempts     INTEGER      NOT NULL DEFAULT 0,
    sent_at      TIMESTAMP    NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);
