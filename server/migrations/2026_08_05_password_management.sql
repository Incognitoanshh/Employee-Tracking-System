-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — password change and reset
--
--  Until now there was no way to change a password at all. An employee who
--  forgot theirs could only be given a new account, which meant losing their
--  attendance and screenshot history along with it.
--
--  Two columns support the new endpoints:
--
--    must_change_password  An admin-issued reset sets this. The client shows
--                          a change-password screen instead of the panel
--                          until the employee picks their own password, so a
--                          temporary password handed over on chat or by phone
--                          cannot stay in use.
--
--    password_changed_at   When the password was last set. Shown to admins,
--                          and the basis for any future "expire after N days"
--                          policy. NULL means "never changed since the
--                          account was created".
--
--  Nothing here touches existing passwords. Both columns are additive with
--  safe defaults, so the running server keeps working before the new build
--  is installed.
--
--  Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE employees
    ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMP WITHOUT TIME ZONE;

COMMIT;
