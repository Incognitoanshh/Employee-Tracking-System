-- Every timestamp default writes UTC, whatever timezone the server is in.
--
-- THE CONVENTION THIS RESTORES. Everything in this schema except
-- active_sessions is TIMESTAMP WITHOUT TIME ZONE holding UTC, and everything
-- that reads it — utils/ist_sql.js, presence.js, every report — adds 5:30 to
-- get an IST day. config/db.js already says this in the comment under its
-- type parsers.
--
-- WHAT WAS ACTUALLY HAPPENING. The columns defaulted to plain `now()`, which
-- is a timestamptz. Assigning it to a naive column converts it USING THE
-- SESSION'S TIMEZONE — so the value stored was the server's wall clock, not
-- UTC. The column says UTC, the reader assumes UTC, and the writer wrote
-- whatever the machine's clock displayed.
--
-- On a UTC server the two are identical, which is why this survived: the
-- production box runs UTC and every row is correct there. It appeared the
-- moment the same code ran on a laptop at +05:30 — a screenshot inserted at
-- 21:27 IST was stored as 21:27, read as UTC, and counted as belonging to the
-- NEXT IST day. A test that had passed all morning began failing after 18:30
-- UTC, on unchanged code.
--
-- So the bug is real, latent, and one `TZ=` away from being live. It costs
-- one line per column to remove permanently.
--
-- WHAT THIS DOES NOT DO
--
--   * It does not touch existing rows. On the production server they were
--     written in UTC already, because that machine is in UTC; anywhere else
--     the old rows are wrong and this migration cannot know by how much.
--   * It does not touch active_sessions, which is timestamptz on purpose —
--     the one table that carries its own zone.
--   * It does not change any read path. Nothing here alters what an existing
--     query answers on a UTC server: `now()` and `now() AT TIME ZONE 'UTC'`
--     are the same instant there.

ALTER TABLE activity_logs    ALTER COLUMN created_at   SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE app_settings     ALTER COLUMN updated_at   SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE attendance       ALTER COLUMN created_at   SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE channel_members  ALTER COLUMN joined_at    SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE channels         ALTER COLUMN created_at   SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE chat_access_log  ALTER COLUMN viewed_at    SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE employee_configs ALTER COLUMN created_at   SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE employee_configs ALTER COLUMN updated_at   SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE employees        ALTER COLUMN created_at   SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE holidays         ALTER COLUMN created_at   SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE idle_daily       ALTER COLUMN updated_at   SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE message_edits    ALTER COLUMN edited_at    SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE message_reads    ALTER COLUMN last_read_at SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE notifications    ALTER COLUMN created_at   SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE screenshots      ALTER COLUMN created_at   SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE team_members     ALTER COLUMN joined_at    SET DEFAULT (NOW() AT TIME ZONE 'UTC');
ALTER TABLE teams            ALTER COLUMN created_at   SET DEFAULT (NOW() AT TIME ZONE 'UTC');
