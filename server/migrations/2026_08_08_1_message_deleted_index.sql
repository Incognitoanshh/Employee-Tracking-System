-- Withdrawn messages are looked up by when they were withdrawn, on every poll
-- from every client. Partial, because deleted messages are a tiny fraction of
-- the table and a full index on a column that is NULL for almost every row is
-- mostly wasted pages.
CREATE INDEX IF NOT EXISTS idx_messages_deleted_at
    ON messages (deleted_at)
    WHERE deleted_at IS NOT NULL;
