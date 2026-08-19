-- THE REPLY COUNT WAS A TABLE SCAN, ONCE PER MESSAGE ON SCREEN.
--
-- Opening a channel runs one query, and inside it, for every message in the
-- page, a correlated subquery counts that message's replies:
--
--     (SELECT COUNT(*) FROM messages child WHERE child.reply_to = m.seq ...)
--
-- `reply_to` is a foreign key and Postgres does not index those on its own,
-- so each of those counts was a sequential scan of the whole messages table.
-- Fifty messages on screen meant fifty scans.
--
-- MEASURED, on a scratch database with 60,000 messages in one channel:
--
--     without this index   102.994 ms   Seq Scan on messages child (loops=50)
--     with it                0.584 ms   Index Scan using idx_messages_reply_to
--
-- 176x, and it grows with the table: at ten times the messages the scan is
-- ten times worse, while the index scan barely moves. On the demo data, with
-- eighty messages, it is invisible — which is exactly why it survived.
--
-- PARTIAL, because most messages are not replies. Indexing only the rows
-- where reply_to is set keeps it a fraction of the size, and the subquery's
-- condition (`reply_to = m.seq`) can never match a NULL anyway.

CREATE INDEX IF NOT EXISTS idx_messages_reply_to
    ON messages (reply_to) WHERE reply_to IS NOT NULL;
