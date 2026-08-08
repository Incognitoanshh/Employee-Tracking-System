-- ═══════════════════════════════════════════════════════════════════════════
--  Direct messages — one person to one person, outside any team.
--
--  BUILT ON THE CHANNEL, NOT BESIDE IT. A direct message is a channel with
--  exactly two members and no team. That one decision means replies, edits,
--  deletion, attachments, mentions, pinning, search, unread counts, the
--  gapless `seq` ordering and the whole delivery path all work on day one,
--  because they are already written against channels. A separate
--  `direct_messages` table would have meant writing every one of them again
--  and getting a different half of them wrong.
--
--  WHO CAN SEE ONE
--  Only its two members. This is the one place where the super admin's
--  standing access to every channel does NOT apply — see utils/chat_access.js.
--  Reading somebody's private conversation goes through the audited route in
--  team.controller, which demands a purpose and a reference and writes both to
--  a table that is never purged. That is the owner's decision, taken
--  deliberately: private by default, reachable when there is a reason, and
--  never without a record.
--
--  THE PAIR KEY exists because "is there already a conversation between these
--  two" has to be answerable atomically. Two people opening a chat with each
--  other at the same moment would otherwise create two channels, and each
--  would then be typing into a room the other cannot see.
--
--  Run: psql ... -f migrations/2026_08_08_2_direct_messages.sql
--  Idempotent.
-- ═══════════════════════════════════════════════════════════════════════════

-- Outside the transaction on purpose: adding a value to an enum inside one is
-- only allowed on newer Postgres, and refusing to run at all on an older
-- server is a worse outcome than one statement that is not rolled back.
ALTER TYPE channel_type ADD VALUE IF NOT EXISTS 'DIRECT';

BEGIN;

-- A direct message belongs to no team. Everything that reads channels joins
-- teams, so those joins become LEFT joins in the controllers.
ALTER TABLE channels ALTER COLUMN team_id DROP NOT NULL;

-- The canonical name for a pair, smaller employee_id first, so that A→B and
-- B→A produce the same key and the unique index below can do its job.
ALTER TABLE channels ADD COLUMN IF NOT EXISTS dm_key VARCHAR(120);

-- One conversation per pair, enforced by the database rather than by a
-- read-then-write in the application. Partial, because it says nothing about
-- team channels.
CREATE UNIQUE INDEX IF NOT EXISTS channels_dm_key_idx
    ON channels (dm_key)
    WHERE dm_key IS NOT NULL;

-- Listing somebody's conversations is "which DM channels am I a member of",
-- asked on every sidebar draw.
CREATE INDEX IF NOT EXISTS channel_members_employee_idx
    ON channel_members (employee_id);

COMMIT;
