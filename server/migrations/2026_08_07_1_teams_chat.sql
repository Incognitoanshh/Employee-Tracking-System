-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — Teams and chat (Phase 1 schema)
--
--  A team holds channels; a channel holds messages. That shape is here from
--  the first day on purpose. "One team, one conversation" is easier to build
--  and wrong within a few months — a team of twelve puts everything in one
--  stream and people stop reading it. Adding channels later would mean a
--  migration over the largest table in the system, so `channel_id` exists on
--  `messages` from the start.
--
--  WHAT THIS SCHEMA IS BUILT AROUND
--
--  1. The record survives the people. An employee who leaves takes their
--     account with them but not their words: `sender_id` goes NULL while
--     `sender_name` — captured at send time — stays. Without that snapshot
--     three former employees all read as "Removed User" and a conversation
--     nobody can attribute is no use to an HR review, which is the one time
--     anybody will read it.
--
--  2. Nothing is edited away silently. Deleting is not offered at all, and
--     editing keeps every previous version in `message_edits`. An edit window
--     without versioning is a delete with extra steps: type something, change
--     it to "." thirty seconds later, and the original is gone while the log
--     still claims to be complete.
--
--  3. Delivery is by cursor, not by push. Every message gets a global `seq`
--     and a client asks "what is there after 4821?" — one indexed query
--     covering all of that person's channels at once, whether they are in one
--     team or five. See the note on ordering above `messages`, which is the
--     subtle part.
--
--  Idempotent. Safe to re-run.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── channel_type ───────────────────────────────────────────────────────────
--  An enum rather than free text so a typo cannot invent a third kind of
--  channel that no permission check knows about. ALTER TYPE ... ADD VALUE
--  covers the ones already anticipated (READ_ONLY, SYSTEM, BOT).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'channel_type') THEN
        CREATE TYPE channel_type AS ENUM ('STANDARD', 'ANNOUNCEMENT');
    END IF;
END $$;

-- ── teams ──────────────────────────────────────────────────────────────────
--  Archived, never deleted. Deleting a team would take its entire history
--  with it, which contradicts the point of keeping chat forever. Archiving
--  makes it read-only: still searchable, still readable, closed to new
--  messages.
--
--  archived_reason is stored because "why is this team closed" is asked
--  months later and an unexplained boolean is a bad answer — the same reason
--  suspended_by exists on employees.
CREATE TABLE IF NOT EXISTS teams (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(120) NOT NULL,
    description     TEXT,
    created_by      VARCHAR(50) REFERENCES employees(employee_id) ON DELETE SET NULL,
    created_at      TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    is_archived     BOOLEAN NOT NULL DEFAULT false,
    archived_at     TIMESTAMP WITHOUT TIME ZONE,
    archived_by     VARCHAR(50) REFERENCES employees(employee_id) ON DELETE SET NULL,
    archived_reason TEXT
);

-- Two teams called "Development" is a support ticket waiting to happen.
-- Case-insensitive, matching how usernames are already treated.
CREATE UNIQUE INDEX IF NOT EXISTS teams_name_lower_idx ON teams (LOWER(name));

-- ── channels ───────────────────────────────────────────────────────────────
--  is_private is here in Phase 1 although private channels arrive in Phase 2:
--  the column costs nothing now and adding it later means touching a table
--  that every message references.
CREATE TABLE IF NOT EXISTS channels (
    id           SERIAL PRIMARY KEY,
    team_id      INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    name         VARCHAR(120) NOT NULL,
    description  TEXT,
    type         channel_type NOT NULL DEFAULT 'STANDARD',
    is_default   BOOLEAN NOT NULL DEFAULT false,
    is_private   BOOLEAN NOT NULL DEFAULT false,
    created_by   VARCHAR(50) REFERENCES employees(employee_id) ON DELETE SET NULL,
    created_at   TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS channels_team_name_idx
    ON channels (team_id, LOWER(name));

-- Exactly one General per team. The default channel is the one every member
-- is in without being added, so a second one would silently split the team.
CREATE UNIQUE INDEX IF NOT EXISTS channels_one_default_idx
    ON channels (team_id) WHERE is_default;

CREATE INDEX IF NOT EXISTS channels_team_idx ON channels (team_id);

-- ── team_members ───────────────────────────────────────────────────────────
--  No role column. ETS already carries a role on `employees` and the rules
--  are enforced from there; a second, team-local role would eventually
--  disagree with the first, and the disagreement would be a security bug
--  rather than a cosmetic one.
--
--  CASCADE on delete: when an account is removed its memberships go with it.
--  Its messages do not — see `messages`.
CREATE TABLE IF NOT EXISTS team_members (
    team_id     INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    employee_id VARCHAR(50) NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
    joined_at   TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (team_id, employee_id)
);

-- "Which teams am I in" runs on every poll, so it gets its own index; the
-- primary key only helps the other direction.
CREATE INDEX IF NOT EXISTS team_members_employee_idx ON team_members (employee_id);

-- ── channel_members ────────────────────────────────────────────────────────
--  Who may see a channel that is not the team's General.
--
--  Access is: in the team, AND (the channel is the default one OR there is a
--  row here). Being added to a team therefore gets you General and nothing
--  else — every other channel is granted deliberately.
--
--  This is stricter than Teams, where every standard channel in a team is
--  visible to all its members. It is the stricter reading of the requirement
--  ("only General and assigned channels; an employee should not even see
--  another department's channel names") and the safer default for a product
--  whose whole subject is who can see what. `is_private` on channels is
--  therefore a label for the interface, not the mechanism — the mechanism is
--  this table, uniformly.
CREATE TABLE IF NOT EXISTS channel_members (
    channel_id  INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    employee_id VARCHAR(50) NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
    joined_at   TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (channel_id, employee_id)
);

CREATE INDEX IF NOT EXISTS channel_members_employee_idx
    ON channel_members (employee_id);

-- ── messages ───────────────────────────────────────────────────────────────
--  ON `seq` AND ORDERING — the subtle part of this schema.
--
--  `seq` is what clients poll against: "give me everything after 4821". For
--  that to be safe, a client that has seen seq N must be certain nothing
--  below N can still appear. A bare sequence does NOT guarantee that. Two
--  concurrent senders take 100 and 101; if 101 commits first, a poll can
--  return 101, the client advances its cursor past 100, and 100 is then
--  committed and never delivered to anyone. The message is in the database
--  and on nobody's screen — the worst kind of failure, because it looks
--  like nothing happened.
--
--  So inserts serialise on one advisory lock (see chat.controller), which
--  makes commit order match `seq` order. The lock is held for the duration
--  of a single INSERT, so this ceases to be cheap somewhere north of a
--  thousand messages a second — far beyond anything this deployment will
--  see, and the alternative (per-channel sequences, or tracking in-flight
--  transaction ids) is a great deal of machinery for a problem nobody here
--  has.
--
--  ON THE SNAPSHOT COLUMNS — sender_name and sender_employee_code are copied
--  in at send time and never updated. If somebody's name changes, old
--  messages keep the name they were sent under, which is what Slack and
--  Teams do and what an investigation needs.
--
--  ON deleted_at / deleted_by — nothing writes these. Deleting a message is
--  deliberately not offered. They exist so that if a policy-driven removal
--  is ever required (a legal demand, say), it is a controller change rather
--  than a migration over the biggest table in the system.
CREATE TABLE IF NOT EXISTS messages (
    seq                  BIGSERIAL PRIMARY KEY,
    channel_id           INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,

    sender_id            VARCHAR(50) REFERENCES employees(employee_id) ON DELETE SET NULL,
    sender_name          TEXT NOT NULL,
    sender_employee_code VARCHAR(50),

    body                 TEXT NOT NULL CHECK (LENGTH(body) BETWEEN 1 AND 2000),
    reply_to             BIGINT REFERENCES messages(seq) ON DELETE SET NULL,

    -- Sent by the client, and the reason the offline queue is safe.
    --
    -- A queued message is retried until the server confirms it. The failure
    -- that needs guarding against is the one where the FIRST attempt actually
    -- arrived and only the response was lost: the client, having heard
    -- nothing, sends it again. Without this the conversation quietly grows
    -- duplicates, and only on bad connections — which is exactly where nobody
    -- is watching closely.
    --
    -- The client generates the id once, when the message is composed, and
    -- reuses it for every retry. The unique index below turns the second
    -- arrival into a no-op that returns the first one's seq.
    client_msg_id        UUID,

    -- clock_timestamp(), NOT NOW(). NOW() is the moment the TRANSACTION
    -- began, and the send lock is taken after that, so two messages can carry
    -- timestamps in one order and sequence numbers in the other. The panel
    -- sorts by time and the delivery cursor walks by seq; if those two
    -- disagree, a message appears above the one it was actually a reply to.
    -- clock_timestamp() is read at the moment of insert, inside the lock, so
    -- both orders are the same order.
    created_at           TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT clock_timestamp(),
    edited_at            TIMESTAMP WITHOUT TIME ZONE,
    edit_count           INTEGER NOT NULL DEFAULT 0,

    deleted_at           TIMESTAMP WITHOUT TIME ZONE,
    deleted_by           VARCHAR(50) REFERENCES employees(employee_id) ON DELETE SET NULL,

    -- A generated column rather than a trigger: it cannot drift out of sync
    -- with `body`, because there is no window in which it is written
    -- separately.
    --
    -- 'simple' and NOT 'english', because people here type Hinglish.
    --
    -- The reason is stopwords, not stemming. English stemming leaves Hindi
    -- words alone; the stoplist does not. Measured on this schema:
    --
    --     'ye kaam me kar do na'
    --        english -> ye, kaam, kar, na        ← "me" and "do" removed
    --        simple  -> ye, kaam, me, kar, do, na
    --
    -- "kar do", "me", "to", "no" are everyday words here and 'english'
    -- silently discards them as noise, so a search for them returns nothing
    -- and looks like the message was never sent.
    --
    -- What 'simple' gives up is stemming — a search for "report" not finding
    -- "reports". Prefix matching recovers that without a stemmer, and still
    -- uses the GIN index below:  to_tsquery('simple', 'report:*'). That is
    -- how chat.controller builds every query, and it works the same for
    -- Hindi words, which no stemmer would have handled at all.
    body_tsv             TSVECTOR GENERATED ALWAYS AS
                             (to_tsvector('simple'::regconfig, body)) STORED
);

-- The delivery query: "messages in these channels, after this seq".
CREATE INDEX IF NOT EXISTS messages_channel_seq_idx ON messages (channel_id, seq);

-- Retry deduplication. Partial, because only queued messages carry an id.
CREATE UNIQUE INDEX IF NOT EXISTS messages_client_msg_id_idx
    ON messages (channel_id, client_msg_id) WHERE client_msg_id IS NOT NULL;

-- Search.
CREATE INDEX IF NOT EXISTS messages_body_tsv_idx ON messages USING GIN (body_tsv);

-- Reading a conversation from the bottom up.
CREATE INDEX IF NOT EXISTS messages_channel_created_idx
    ON messages (channel_id, created_at DESC);

-- ── message_edits ──────────────────────────────────────────────────────────
--  Every version an edited message has had. `edited_by` is recorded rather
--  than assumed to be the sender, so that if editing by an administrator is
--  ever allowed the history already says who did it.
CREATE TABLE IF NOT EXISTS message_edits (
    id          BIGSERIAL PRIMARY KEY,
    message_seq BIGINT NOT NULL REFERENCES messages(seq) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    old_body    TEXT NOT NULL,
    edited_by   VARCHAR(50) REFERENCES employees(employee_id) ON DELETE SET NULL,
    edited_name TEXT NOT NULL,
    edited_at   TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    UNIQUE (message_seq, version)
);

CREATE INDEX IF NOT EXISTS message_edits_message_idx ON message_edits (message_seq);

-- ── message_reads ──────────────────────────────────────────────────────────
--  One row per person per channel holding how far they have read. Unread is
--  then a count of messages past that mark.
--
--  Deliberately NOT per-message read receipts. "Seen by Rahul, seen by Amit"
--  costs a row per message per member — a team of ten turns one message into
--  ten more rows — and in a workplace it mostly serves to let people check
--  whether a colleague is ignoring them.
CREATE TABLE IF NOT EXISTS message_reads (
    employee_id   VARCHAR(50) NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
    channel_id    INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    last_read_seq BIGINT NOT NULL DEFAULT 0,
    last_read_at  TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (employee_id, channel_id)
);

-- ── notifications ──────────────────────────────────────────────────────────
--  Minimal on purpose. Ordinary unread counts come from message_reads and do
--  not need rows here. This table is for the things that must be noticed
--  individually rather than counted — an announcement, and from Phase 2 an
--  @mention — where "you have 4 unread" is not good enough because one of
--  those four was addressed to you by name.
CREATE TABLE IF NOT EXISTS notifications (
    id          BIGSERIAL PRIMARY KEY,
    employee_id VARCHAR(50) NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
    type        VARCHAR(30) NOT NULL,          -- ANNOUNCEMENT | MENTION (Phase 2)
    message_seq BIGINT REFERENCES messages(seq) ON DELETE CASCADE,
    channel_id  INTEGER REFERENCES channels(id) ON DELETE CASCADE,
    is_read     BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

-- Only the unread ones are ever queried, so the index only covers those.
CREATE INDEX IF NOT EXISTS notifications_unread_idx
    ON notifications (employee_id, created_at DESC) WHERE NOT is_read;

-- ── chat_access_log ────────────────────────────────────────────────────────
--  Every time a super admin reads somebody's conversation.
--
--  A structured table rather than a line in activity_logs because the whole
--  point is to be able to answer "how often, by whom, and under what
--  authority" — questions that need columns, not a LIKE over free text.
--  A one-line summary is ALSO written to activity_logs so that these show up
--  in the existing audit report without it having to know about this table;
--  that row is the pointer, this is the detail.
--
--  purpose is a fixed set rather than free text: it keeps the record
--  reportable and stops it filling with "checking" and "test". reference_id
--  is where the real identifier goes — "Complaint #214".
--
--  Never purged. This outlives the retention period of the chat it describes,
--  because a record of who looked is only worth anything if it is complete.
CREATE TABLE IF NOT EXISTS chat_access_log (
    id           BIGSERIAL PRIMARY KEY,
    viewer_id    VARCHAR(50) REFERENCES employees(employee_id) ON DELETE SET NULL,
    viewer_name  TEXT NOT NULL,
    team_id      INTEGER REFERENCES teams(id) ON DELETE SET NULL,
    channel_id   INTEGER REFERENCES channels(id) ON DELETE SET NULL,
    team_name    TEXT,
    channel_name TEXT,
    purpose      VARCHAR(40) NOT NULL,
    reference_id VARCHAR(120),
    note         TEXT,                          -- only when purpose = OTHER
    viewed_at    TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_access_log_viewed_idx
    ON chat_access_log (viewed_at DESC);

COMMIT;
