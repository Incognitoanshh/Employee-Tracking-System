-- Reactions on a message.
--
-- WHY A TABLE RATHER THAN A COLUMN. A JSON blob on `messages` would be one
-- row rewritten by everybody who reacts, which is a write conflict per
-- reaction and no way to ask "who reacted with this". One row per person per
-- emoji answers both, and the primary key IS the rule: a person may react
-- with a given emoji once.
--
-- ON DELETE CASCADE on the message, because a reaction to a deleted message
-- is not evidence of anything — unlike the message itself, which is kept and
-- tombstoned.

CREATE TABLE IF NOT EXISTS message_reactions (
    seq         BIGINT      NOT NULL REFERENCES messages(seq) ON DELETE CASCADE,
    emoji       TEXT        NOT NULL,
    employee_id VARCHAR(50) NOT NULL REFERENCES employees(employee_id)
                                ON UPDATE CASCADE ON DELETE CASCADE,
    created_at  TIMESTAMP    NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    PRIMARY KEY (seq, emoji, employee_id)
);

-- Reading a channel means reading the reactions of every message on screen,
-- so the lookup that matters is "all reactions for these messages".
CREATE INDEX IF NOT EXISTS idx_message_reactions_seq
    ON message_reactions (seq);

-- A SHORT WHITELIST, ENFORCED IN THE APPLICATION, NOT HERE.
--
-- The column is TEXT on purpose: a CHECK constraint listing today's emoji
-- would need a migration every time somebody wants another one, and a
-- rejected reaction would surface as a 500 from a constraint violation
-- rather than as a clear refusal. The controller holds the list.
