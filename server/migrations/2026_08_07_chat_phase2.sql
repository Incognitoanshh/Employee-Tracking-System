-- ═══════════════════════════════════════════════════════════════════════════
--  ETS — chat, Phase 2: mentions, pins and attachments
--
--  Replies and private channels need no schema: `messages.reply_to` and
--  `channels.is_private` were put in during Phase 1 precisely so this
--  migration would not have to touch the largest table in the system twice.
--  Editing needed none either — the history has been kept since Phase 1, it
--  simply had no interface.
--
--  So what is left is three things:
--
--  MENTIONS. Being named is different from being talked near. "You have 14
--  unread" is a number people learn to ignore; "Priya asked you something" is
--  not, and the difference has to survive a restart, which means a row.
--
--  PINS. A channel's few important messages — the VPN address, this week's
--  deadline — otherwise get scrolled past within a day and asked about again
--  every week.
--
--  ATTACHMENTS. Files are encrypted by the client before they are sent, the
--  same way screenshots already are; the server stores bytes it cannot read
--  and the metadata needed to list them. That is not a new mechanism, it is
--  the existing one pointed at a second kind of file.
--
--  Idempotent. Safe to re-run.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── mentions ───────────────────────────────────────────────────────────────
--  One row per person named in a message.
--
--  Recorded separately from the notification it produces: a notification is
--  read and gone, whereas "was I named in this" is a property of the message
--  and stays true afterwards. The panel needs the second to highlight the
--  message every time it is drawn, not only the first time.
CREATE TABLE IF NOT EXISTS mentions (
    id          BIGSERIAL PRIMARY KEY,
    message_seq BIGINT NOT NULL REFERENCES messages(seq) ON DELETE CASCADE,
    employee_id VARCHAR(50) NOT NULL REFERENCES employees(employee_id) ON DELETE CASCADE,
    created_at  TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (message_seq, employee_id)
);

-- "Where was I named" — the query behind the mention badge.
CREATE INDEX IF NOT EXISTS mentions_employee_idx ON mentions (employee_id, message_seq DESC);

-- ── pins ───────────────────────────────────────────────────────────────────
--  Columns rather than a table: a channel has a handful of pinned messages,
--  never thousands, and a join to fetch them would cost more than it saves.
--
--  pinned_by is kept for the same reason archived_reason and suspended_by are:
--  "why is this at the top of the channel" is asked later, and an unattributed
--  pin is a small mystery nobody can resolve.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMP WITHOUT TIME ZONE;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS pinned_by VARCHAR(50)
    REFERENCES employees(employee_id) ON DELETE SET NULL;

-- Partial: only pinned rows are ever looked up this way, and there are very
-- few of them against a table that will hold millions.
CREATE INDEX IF NOT EXISTS messages_pinned_idx
    ON messages (channel_id, pinned_at DESC) WHERE pinned_at IS NOT NULL;

-- ── attachments ────────────────────────────────────────────────────────────
--  The bytes live on disk beside the screenshots and are encrypted by the
--  client before they leave the machine, so the server holds a file it cannot
--  open. This table is the metadata: what it was called, how big, and which
--  message it belongs to.
--
--  file_name is what the person called it, shown in the panel. stored_name is
--  what it is on disk — generated, never client-controlled, because a name
--  that arrives over the network and is then used as a path is how directory
--  traversal happens. The screenshot upload learned that already.
--
--  ON DELETE CASCADE from messages, but nothing deletes messages: this exists
--  so the constraint is honest rather than because it will fire.
--  message_seq is NULLABLE, and that is the whole upload sequence. The file
--  goes up first and the message that carries it is sent afterwards, because
--  the alternative — send an empty message, then attach to it — puts a blank
--  line in the conversation for however long the upload takes, and leaves one
--  there permanently if the upload then fails.
--
--  So a freshly uploaded file has no message yet. It is claimed when the
--  message is sent. One that is never claimed — the employee changed their
--  mind, or closed the panel mid-upload — is an orphan, and purge_old_data
--  sweeps those up rather than leaving them on disk forever.
CREATE TABLE IF NOT EXISTS attachments (
    id           BIGSERIAL PRIMARY KEY,
    message_seq  BIGINT REFERENCES messages(seq) ON DELETE CASCADE,
    channel_id   INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    file_name    TEXT NOT NULL,
    stored_name  TEXT NOT NULL UNIQUE,
    mime_type    VARCHAR(120),
    size_bytes   BIGINT NOT NULL,
    uploaded_by  VARCHAR(50) REFERENCES employees(employee_id) ON DELETE SET NULL,
    created_at   TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS attachments_message_idx ON attachments (message_seq);

-- Finding orphans to sweep: uploaded, never attached to anything.
CREATE INDEX IF NOT EXISTS attachments_orphan_idx
    ON attachments (created_at) WHERE message_seq IS NULL;

-- An upload arrives before the message that carries it exists, so the body is
-- allowed to be empty when there is a file. The Phase 1 constraint required
-- at least one character, which would have made "here" the shortest possible
-- way to send a photograph.
ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_body_check;
ALTER TABLE messages ADD CONSTRAINT messages_body_check
    CHECK (LENGTH(body) <= 2000);

COMMIT;
