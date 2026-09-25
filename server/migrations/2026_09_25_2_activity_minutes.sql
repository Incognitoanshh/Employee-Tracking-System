-- What kind of minute it was, per employee: how much keyboard, how much
-- mouse, how many changes of application, and the score those add up to.
--
-- WHY THE PARTS AND NOT JUST THE SCORE. "Score 12" tells an administrator
-- nothing they can act on. "No keystrokes at all in half an hour, and 58
-- movements spaced identically" is a sentence they can take to the person —
-- and the same numbers are what would show the score to be wrong, which
-- matters more, because this is an estimate about somebody's working day.
CREATE TABLE IF NOT EXISTS activity_minutes (
    id                   BIGSERIAL PRIMARY KEY,
    employee_id          VARCHAR(50) NOT NULL,
    minute               TIMESTAMP   NOT NULL,
    score                SMALLINT    NOT NULL,
    band                 VARCHAR(10) NOT NULL,
    keystrokes           INTEGER     NOT NULL DEFAULT 0,
    clicks               INTEGER     NOT NULL DEFAULT 0,
    scrolls              INTEGER     NOT NULL DEFAULT 0,
    mouse_moves          INTEGER     NOT NULL DEFAULT 0,
    window_changes       INTEGER     NOT NULL DEFAULT 0,
    automation_suspected BOOLEAN     NOT NULL DEFAULT FALSE,
    reasons              TEXT,
    created_at           TIMESTAMP   NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);

-- One row per employee per minute, so a client that re-sends a batch after a
-- network failure cannot double the day.
CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_minutes_unique
    ON activity_minutes (employee_id, minute);

-- The alert rule asks "the last N minutes for this person", every time the
-- panel is opened.
CREATE INDEX IF NOT EXISTS idx_activity_minutes_recent
    ON activity_minutes (employee_id, minute DESC);
