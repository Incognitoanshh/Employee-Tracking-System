-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — usernames stop being case-sensitive
--
--  Login compared usernames exactly, so the super admin registered as
--  "Amazeinternet" could not sign in by typing "amazeinternet". The password
--  was right, the account existed, and the only feedback was "Invalid
--  credentials" — which reads as a wrong password. Somebody hitting that in
--  the field would keep retrying and eventually lock themselves out.
--
--  WHY THE INDEX MATTERS AS MUCH AS THE QUERY
--  The UNIQUE constraint on employees.username is case-sensitive too, so
--  "admin" and "Admin" can both exist. Making only the LOGIN case-insensitive
--  would turn that into a way in: register "Admin", and a login for "admin"
--  now matches two rows. Which row wins is up to the planner.
--
--  So the uniqueness has to move first. This adds a unique index on
--  LOWER(username), which makes a case-variant account impossible to create
--  from here on.
--
--  IF THE INDEX CANNOT BE CREATED, NOTHING ELSE CHANGES. An existing pair
--  that differs only by case would make the index fail — and rather than
--  leave the database half-migrated, this reports the offending usernames
--  and stops. Resolve them by renaming one, then run this again.
--
--  Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

DO $$
DECLARE
    clash TEXT;
BEGIN
    SELECT string_agg(names, ' | ') INTO clash
    FROM (
        SELECT string_agg(username, ', ') AS names
        FROM employees
        GROUP BY LOWER(username)
        HAVING COUNT(*) > 1
    ) collisions;

    IF clash IS NOT NULL THEN
        RAISE EXCEPTION
            'Usernames differing only by case already exist: %. '
            'Rename one of each pair, then run this migration again.', clash;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS employees_username_lower_idx
    ON employees (LOWER(username));

COMMIT;
