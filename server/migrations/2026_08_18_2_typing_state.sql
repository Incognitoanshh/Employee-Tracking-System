-- Who is typing, right now.
--
-- WHY A TABLE AND NOT A VARIABLE IN THE PROCESS. An in-memory map is the
-- obvious shape for something this short-lived, and it works until the server
-- restarts or somebody runs a second instance behind pm2 — at which point two
-- people in the same conversation are told different things by whichever
-- process answered. A table is the same thing the rest of the product already
-- agrees through.
--
-- EVERY ROW CARRIES ITS OWN EXPIRY. Typing is a statement about the next few
-- seconds; a row without one is a "typing…" that never goes away when
-- somebody closes their laptop mid-sentence, which is the failure everybody
-- has seen in some chat app and nobody can explain.
--
-- One row per person per channel: the primary key is the rule.

CREATE TABLE IF NOT EXISTS typing_state (
    channel_id  INTEGER     NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    employee_id VARCHAR(50) NOT NULL REFERENCES employees(employee_id)
                                ON UPDATE CASCADE ON DELETE CASCADE,
    expires_at  TIMESTAMP   NOT NULL,
    PRIMARY KEY (channel_id, employee_id)
);

-- The only question ever asked of this table: who is typing in THIS channel,
-- and has it lapsed.
CREATE INDEX IF NOT EXISTS idx_typing_state_live
    ON typing_state (channel_id, expires_at);
