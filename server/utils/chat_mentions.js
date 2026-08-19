/**
 * Working out who a message names.
 *
 * Two sources, deliberately, because either one alone is wrong:
 *
 *   THE CLIENT'S LIST. The panel has an autocomplete, so when somebody picks
 *   a name out of it the employee_id is known exactly. Names contain spaces
 *   ("Rajesh Kumar"), so trying to recover that from the text afterwards
 *   means guessing where the name ends — and guessing wrong either misses
 *   the mention or swallows the next word.
 *
 *   THE TEXT. People type "@rajesh" from memory without touching the
 *   autocomplete, and a mention that silently does nothing is worse than no
 *   mention feature at all: the sender believes they have got somebody's
 *   attention. Usernames have no spaces, so these can be found reliably.
 *
 * Both are then filtered to people who can actually see the channel. A
 * mention is a notification, and notifying somebody about a conversation they
 * are not in tells them it exists — which is the one thing the visibility
 * rules are for.
 */

const { visibleChannelSql } = require("./chat_access");

/** Usernames written as @name in the body. Case is ignored. */
function parseHandles(body) {
    const found = new Set();
    // Bounded to sane username characters, and capped: a message that is
    // nothing but @s should not turn into a hundred lookups.
    for (const match of String(body || "").matchAll(/(^|[^\w])@([a-zA-Z0-9._-]{2,50})/g)) {
        found.add(match[2].toLowerCase());
        if (found.size >= 20) break;
    }
    return [...found];
}

/**
 * Who this message actually mentions.
 *
 * @returns {Promise<string[]>} employee_ids, never including the sender —
 *   notifying somebody about their own message is noise, and people do write
 *   their own name.
 */
async function resolveMentions(db, { channelId, senderId, body, explicitIds }) {
    const handles = parseHandles(body);
    const ids = Array.isArray(explicitIds) ? explicitIds.map(String).slice(0, 50) : [];
    if (handles.length === 0 && ids.length === 0) return [];

    // The visibility test is written against `e.employee_id`, not a bound
    // parameter: the question is whether the MENTIONED person can see this
    // channel, not whether the sender can. Testing the sender would notify
    // people about conversations they are not part of, which is the one thing
    // the access rules exist to prevent.
    const result = await db.query(
        `SELECT DISTINCT e.employee_id
           FROM employees e
          -- A HANDLE MAY BE A USERNAME **OR** AN EMPLOYEE ID.
          --
          -- The panel writes "@username", but people type "@26AMZEM001" from
          -- memory — and for a while the panel itself did, because the member
          -- list was sent without usernames. Every one of those mentions
          -- resolved to nobody: no highlight, no notification, and no sign
          -- that anything had failed. Matching the id as well costs one
          -- comparison and closes both cases.
          WHERE (e.employee_id = ANY($1)
                 OR LOWER(e.username) = ANY($2)
                 OR LOWER(e.employee_id) = ANY($2))
            AND e.employee_id <> $3
            AND NOT e.suspended
            AND EXISTS (
                SELECT 1 FROM channels c
                 WHERE c.id = $4 AND ${visibleChannelSql("c", "e.employee_id")}
            )`,
        [ids, handles, senderId, channelId]
    );
    return result.rows.map((row) => row.employee_id);
}

module.exports = { parseHandles, resolveMentions };
