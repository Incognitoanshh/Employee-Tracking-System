/**
 * Chat — the employee-facing half.
 *
 * Administration of teams and channels lives in team.controller; this file is
 * what the employee panel talks to. Two things in here are worth understanding
 * before changing anything:
 *
 * DELIVERY IS A CURSOR, NOT A PUSH.
 * There is no socket. The client holds the highest `seq` it has seen and asks
 * "what is there after this?" — one indexed query that covers every channel
 * the person can see, whether that is one or fifteen. Polling was chosen over
 * WebSockets deliberately: the link this runs over shows around 20% packet
 * loss, where a socket spends its life reconnecting and a dropped poll costs
 * nothing because the next one asks the same question.
 *
 * SEQ MUST BE GAPLESS FROM A READER'S POINT OF VIEW.
 * A bare BIGSERIAL is not enough, and the failure is silent. Two people send
 * at once; one takes seq 100 and the other 101; 101 commits first. A poll
 * returns 101, the client advances its cursor past 100, and 100 then commits
 * and is delivered to nobody — stored in the database, never on a screen.
 * Measured, not theorised. So every insert takes SEND_LOCK for the length of
 * one statement, which makes commit order match seq order. See sendMessage.
 */

const pool = require("../config/db");
const { visibleChannelSql, loadVisibleChannel } = require("../utils/chat_access");
const { resolveMentions } = require("../utils/chat_mentions");
const path = require("path");
const fs = require("fs");

const MAX_BODY = 2000;
const EDIT_WINDOW_MINUTES = 5;
const RATE_LIMIT_PER_MINUTE = 20;
const PAGE_SIZE = 50;
const MAX_PAGE_SIZE = 200;
const MAX_PINNED = 20;
const MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024;

/**
 * The one lock every message insert serialises on. A constant, because the
 * ordering guarantee is global — per-channel locks would let two channels
 * interleave and reintroduce the gap for a client polling across both.
 */
const SEND_LOCK = 0x45545301;

/**
 * Sends per employee in the last minute.
 *
 * In memory, so a restart forgives everyone. That is acceptable: this exists
 * to stop a stuck client hammering the server and to blunt someone pasting a
 * wall of lines, not to enforce policy. Anything that needs to survive a
 * restart does not belong in a rate limiter.
 */
const recentSends = new Map();

function overRateLimit(employeeId) {
    const now = Date.now();
    const window = (recentSends.get(employeeId) || []).filter((t) => now - t < 60_000);
    if (window.length >= RATE_LIMIT_PER_MINUTE) {
        recentSends.set(employeeId, window);
        return true;
    }
    window.push(now);
    recentSends.set(employeeId, window);
    return false;
}

// Left unbounded it would hold a row per employee forever. Cheap sweep.
setInterval(() => {
    const now = Date.now();
    for (const [id, times] of recentSends) {
        const live = times.filter((t) => now - t < 60_000);
        if (live.length) recentSends.set(id, live);
        else recentSends.delete(id);
    }
}, 5 * 60_000).unref();

const fail = (res, status, message) => res.status(status).json({ success: false, message });

function serverError(res, req, err) {
    console.error("[500]", req.method, req.originalUrl, err.message);
    return res.status(500).json({ success: false, message: "Internal server error" });
}

/** Shape a message row for the client. */
function toMessage(row) {
    return {
        seq:          Number(row.seq),
        channel_id:   row.channel_id,
        sender_id:    row.sender_id,
        // An account that has been deleted keeps the name it sent under.
        // Without this every former employee reads as the same anonymous
        // person and a conversation cannot be attributed at all.
        sender_name:  row.sender_name,
        sender_code:  row.sender_employee_code,
        former:       row.sender_id === null,
        body:         row.body,
        reply_to:     row.reply_to === null ? null : Number(row.reply_to),
        created_at:   row.created_at,
        edited:       row.edit_count > 0,
        edit_count:   row.edit_count,
        pinned:       Boolean(row.pinned_at),
        pinned_at:    row.pinned_at || null,
        // Filled in by enrich(). Present as empty rather than absent so the
        // panel never has to test for undefined before iterating.
        attachments:  [],
        mentions:     [],
        mentions_me:  false,
        reply:        null,
    };
}

/**
 * Attach the things that live in other tables: files, who was named, and a
 * preview of whatever a message replies to.
 *
 * Done in three queries for the whole page rather than three per message. At
 * a page of fifty that is the difference between three round trips and a
 * hundred and fifty, which on this connection is the difference between a
 * channel that opens and one that appears to hang.
 */
async function enrich(rows, me) {
    if (!rows.length) return rows;
    const seqs = rows.map((m) => m.seq);
    const byseq = new Map(rows.map((m) => [m.seq, m]));

    const files = await pool.query(
        `SELECT id, message_seq, file_name, mime_type, size_bytes
           FROM attachments WHERE message_seq = ANY($1) ORDER BY id`, [seqs]);
    for (const file of files.rows) {
        byseq.get(Number(file.message_seq))?.attachments.push({
            id: Number(file.id),
            file_name: file.file_name,
            mime_type: file.mime_type,
            size_bytes: Number(file.size_bytes),
        });
    }

    const named = await pool.query(
        `SELECT m.message_seq, m.employee_id,
                COALESCE(NULLIF(e.full_name, ''), e.username) AS name
           FROM mentions m
           LEFT JOIN employees e ON e.employee_id = m.employee_id
          WHERE m.message_seq = ANY($1)`, [seqs]);
    for (const row of named.rows) {
        const message = byseq.get(Number(row.message_seq));
        if (!message) continue;
        message.mentions.push({ employee_id: row.employee_id, name: row.name });
        if (row.employee_id === me) message.mentions_me = true;
    }

    // The message being replied to, as a one-line preview. Fetched without a
    // visibility check on purpose: a reply can only point at something in the
    // same channel, and the caller has already established access to that.
    const replyTargets = rows.map((m) => m.reply_to).filter(Boolean);
    if (replyTargets.length) {
        const parents = await pool.query(
            `SELECT seq, sender_name, body FROM messages WHERE seq = ANY($1)`,
            [replyTargets]);
        const byParent = new Map(parents.rows.map((r) => [Number(r.seq), r]));
        for (const message of rows) {
            const parent = message.reply_to && byParent.get(message.reply_to);
            if (!parent) continue;
            message.reply = {
                seq: Number(parent.seq),
                sender_name: parent.sender_name,
                excerpt: String(parent.body || "").slice(0, 140),
            };
        }
    }
    return rows;
}

const MESSAGE_COLUMNS = `
    m.seq, m.channel_id, m.sender_id, m.sender_name, m.sender_employee_code,
    m.body, m.reply_to, m.created_at, m.edit_count, m.pinned_at`;

// ───────────────────────────────────────────────────────────────────────────
//  What the employee can see
// ───────────────────────────────────────────────────────────────────────────

/**
 * GET /api/chat/me/teams
 *
 * Every team and channel this person may see, with how much is unread in
 * each. This is the whole left-hand side of the chat panel in one request —
 * on a link this slow, three requests to draw one screen is three chances to
 * show a half-built interface.
 */
exports.getMyTeams = async (req, res) => {
    const me = req.employee?.employee_id;
    if (!me) return fail(res, 401, "Unauthenticated");

    try {
        const result = await pool.query(
            `SELECT t.id   AS team_id,   t.name AS team_name, t.is_archived,
                    c.id   AS channel_id, c.name AS channel_name,
                    c.type AS channel_type, c.is_default, c.is_private,
                    COALESCE(r.last_read_seq, 0) AS last_read_seq,
                    (SELECT COUNT(*) FROM messages m
                      WHERE m.channel_id = c.id
                        AND m.seq > COALESCE(r.last_read_seq, 0)
                        AND m.deleted_at IS NULL
                        AND m.sender_id IS DISTINCT FROM $1) AS unread,
                    (SELECT MAX(m.seq) FROM messages m WHERE m.channel_id = c.id) AS last_seq,
                    (SELECT MAX(m.created_at) FROM messages m WHERE m.channel_id = c.id)
                        AS last_message_at
               FROM channels c
               JOIN teams t ON t.id = c.team_id
               LEFT JOIN message_reads r
                      ON r.channel_id = c.id AND r.employee_id = $1
              WHERE ${visibleChannelSql("c", 1)}
              ORDER BY t.name, c.is_default DESC, c.name`,
            [me]
        );

        const teams = new Map();
        for (const row of result.rows) {
            if (!teams.has(row.team_id)) {
                teams.set(row.team_id, {
                    id: row.team_id,
                    name: row.team_name,
                    is_archived: row.is_archived,
                    channels: [],
                    unread: 0,
                });
            }
            const team = teams.get(row.team_id);
            const unread = Number(row.unread);
            team.channels.push({
                id:              row.channel_id,
                name:            row.channel_name,
                type:            row.channel_type,
                is_default:      row.is_default,
                is_private:      row.is_private,
                unread,
                last_read_seq:   Number(row.last_read_seq),
                last_seq:        row.last_seq === null ? 0 : Number(row.last_seq),
                last_message_at: row.last_message_at,
            });
            team.unread += unread;
        }

        const notif = await pool.query(
            `SELECT COUNT(*) AS n FROM notifications
              WHERE employee_id = $1 AND NOT is_read`,
            [me]
        );

        return res.json({
            success: true,
            teams: [...teams.values()],
            notifications_unread: Number(notif.rows[0].n),
        });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/**
 * GET /api/chat/updates?since=<seq>
 *
 * The poll. Everything after `since` across all visible channels, plus
 * anything that needs to be noticed individually rather than counted.
 *
 * `since=0` deliberately returns nothing but the current high-water mark. A
 * fresh client should establish its cursor here and then load history per
 * channel, rather than have the server decide to send it a year of messages.
 */
exports.getUpdates = async (req, res) => {
    const me = req.employee?.employee_id;
    if (!me) return fail(res, 401, "Unauthenticated");

    const since = Number.parseInt(req.query.since, 10);
    if (!Number.isFinite(since) || since < 0) {
        return fail(res, 400, "since must be a number");
    }

    try {
        const head = await pool.query(`SELECT COALESCE(MAX(seq), 0) AS head FROM messages`);
        const cursor = Number(head.rows[0].head);

        if (since === 0) {
            return res.json({ success: true, cursor, messages: [], notifications: [] });
        }

        const messages = await pool.query(
            `SELECT ${MESSAGE_COLUMNS}
               FROM messages m
               JOIN channels c ON c.id = m.channel_id
              WHERE m.seq > $2 AND m.deleted_at IS NULL AND ${visibleChannelSql("c", 1)}
              ORDER BY m.seq
              LIMIT 500`,
            [me, since]
        );

        const notifications = await pool.query(
            `SELECT n.id, n.type, n.message_seq, n.channel_id, n.created_at,
                    c.name AS channel_name, t.name AS team_name
               FROM notifications n
               LEFT JOIN channels c ON c.id = n.channel_id
               LEFT JOIN teams t ON t.id = c.team_id
              WHERE n.employee_id = $1 AND NOT n.is_read
              ORDER BY n.created_at DESC
              LIMIT 50`,
            [me]
        );

        const rows = messages.rows;
        const shaped = await enrich(rows.map(toMessage), me);
        return res.json({
            success: true,
            // When the batch was capped, the cursor must be the last message
            // actually handed over — not the global head, which would skip
            // everything the LIMIT cut off.
            cursor: rows.length === 500 ? Number(rows[rows.length - 1].seq) : cursor,
            messages: shaped,
            notifications: notifications.rows,
        });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/**
 * GET /api/chat/channels/:id/messages?before=<seq>&limit=
 *
 * History, newest first, walking backwards. Returned oldest-first so the
 * client can append without reversing.
 */
exports.getMessages = async (req, res) => {
    const me = req.employee?.employee_id;
    if (!me) return fail(res, 401, "Unauthenticated");

    const channelId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(channelId)) return fail(res, 400, "Invalid channel");

    const before = req.query.before ? Number.parseInt(req.query.before, 10) : null;
    if (req.query.before && !Number.isFinite(before)) {
        return fail(res, 400, "before must be a number");
    }
    const limit = Math.min(
        Math.max(Number.parseInt(req.query.limit, 10) || PAGE_SIZE, 1), MAX_PAGE_SIZE);

    try {
        const channel = await loadVisibleChannel(pool, me, channelId);
        // 404 rather than 403 on purpose. Telling somebody "you are not
        // allowed in channel 12" confirms channel 12 exists and that they are
        // outside it, which is the one fact this design is trying not to leak.
        if (!channel) return fail(res, 404, "Channel not found");

        const result = await pool.query(
            `SELECT ${MESSAGE_COLUMNS}
               FROM messages m
              WHERE m.channel_id = $1 AND m.deleted_at IS NULL
                AND ($2::BIGINT IS NULL OR m.seq < $2)
              ORDER BY m.seq DESC
              LIMIT $3`,
            [channelId, before, limit]
        );

        const rows = result.rows.reverse();
        const shaped = await enrich(rows.map(toMessage), me);
        return res.json({
            success: true,
            channel: {
                id: channel.id, name: channel.name, type: channel.type,
                team_id: channel.team_id, team_name: channel.team_name,
                is_archived: channel.is_archived,
                can_post: channel.type === "STANDARD" && !channel.is_archived,
            },
            messages: shaped,
            has_more: result.rows.length === limit,
        });
    } catch (err) {
        return serverError(res, req, err);
    }
};

// ───────────────────────────────────────────────────────────────────────────
//  Sending
// ───────────────────────────────────────────────────────────────────────────

/**
 * POST /api/chat/channels/:id/messages
 *
 * body: { body, client_msg_id?, reply_to? }
 *
 * `client_msg_id` is what makes the offline queue safe. A queued message is
 * retried until the server confirms it, and the case that needs handling is
 * the one where the first attempt arrived and only the reply was lost: the
 * client, having heard nothing, sends it again. With the id, the second
 * arrival returns the first one's seq instead of adding a duplicate — and
 * duplicates would appear only on bad connections, which is exactly where
 * nobody is watching closely enough to notice.
 */
exports.sendMessage = async (req, res) => {
    const me = req.employee?.employee_id;
    if (!me) return fail(res, 401, "Unauthenticated");

    const channelId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(channelId)) return fail(res, 400, "Invalid channel");

    const body = String(req.body?.body ?? "").trim();
    const clientMsgId = req.body?.client_msg_id || null;
    const replyTo = req.body?.reply_to ? Number(req.body.reply_to) : null;
    const attachmentIds = Array.isArray(req.body?.attachment_ids)
        ? req.body.attachment_ids.map(Number).filter(Number.isFinite).slice(0, 10)
        : [];
    const mentionIds = Array.isArray(req.body?.mentions) ? req.body.mentions : [];

    // A message carrying a file needs no words. Demanding some would make
    // "here" the shortest way to send a photograph.
    if (!body && attachmentIds.length === 0) {
        return fail(res, 400, "Message cannot be empty");
    }
    if (body.length > MAX_BODY) {
        return fail(res, 400, `Message is too long — ${body.length} of ${MAX_BODY} characters`);
    }
    if (clientMsgId && !/^[0-9a-f-]{36}$/i.test(String(clientMsgId))) {
        return fail(res, 400, "client_msg_id must be a UUID");
    }

    // NOTE ON WHERE pool.connect() IS CALLED — it is deliberately not here.
    //
    // A dedicated client is only taken once every check has passed and the
    // transaction is about to begin. Taking it earlier deadlocks the pool:
    // each in-flight send would hold one client while asking the same pool
    // for another (the visibility check, the sender lookup), so once
    // DB_POOL_MAX sends were in flight none of them could obtain a second
    // connection and none would release the one they had. Every request then
    // fails with "timeout exceeded when trying to connect".
    //
    // That is 25 simultaneous senders on the default pool, not some
    // theoretical number — one meeting ending in a fifty-person office. Found
    // by the hundred-sender check in test_chat.
    let client;
    try {
        const channel = await loadVisibleChannel(pool, me, channelId);
        if (!channel) return fail(res, 404, "Channel not found");

        if (channel.is_archived) {
            return fail(res, 409, `${channel.team_name} is archived — it is read-only.`);
        }
        // Announcements are one-way by definition. Administrators post to them
        // through team.controller; nobody replies.
        if (channel.type === "ANNOUNCEMENT") {
            return fail(res, 403, "This is an announcement channel — only administrators can post.");
        }
        if (overRateLimit(me)) {
            return fail(res, 429, "You are sending messages too quickly. Wait a moment.");
        }

        const who = await pool.query(
            `SELECT COALESCE(NULLIF(full_name, ''), username) AS name, employee_id
               FROM employees WHERE employee_id = $1`,
            [me]
        );
        if (who.rows.length === 0) return fail(res, 404, "Sender not found");

        // A reply must point at something in THIS channel.
        //
        // BUG this closes: reply_to was taken on trust and only had to be a
        // valid seq. enrich() then returns the parent's sender and the first
        // 140 characters of its body as a preview — so replying to a seq from
        // another team's channel printed a slice of that conversation inside
        // one you are allowed to read. A number counted upwards in a loop
        // would have walked the company's chat a paragraph at a time, and
        // nothing about it would have looked like an attack.
        if (Number.isFinite(replyTo)) {
            const parent = await pool.query(
                `SELECT 1 FROM messages WHERE seq = $1 AND channel_id = $2`,
                [replyTo, channelId]);
            if (parent.rows.length === 0) {
                return fail(res, 400, "You can only reply to a message in this channel.");
            }
        }

        client = await pool.connect();
        await client.query("BEGIN");
        // Held for this statement only. See the header — without it a message
        // can be committed and never delivered to anybody.
        await client.query("SELECT pg_advisory_xact_lock($1)", [SEND_LOCK]);

        const inserted = await client.query(
            `INSERT INTO messages
                 (channel_id, sender_id, sender_name, sender_employee_code,
                  body, reply_to, client_msg_id)
             VALUES ($1, $2, $3, $4, $5, $6, $7)
             ON CONFLICT (channel_id, client_msg_id) WHERE client_msg_id IS NOT NULL
             DO NOTHING
             RETURNING ${"seq, channel_id, sender_id, sender_name, sender_employee_code, body, reply_to, created_at, edit_count, pinned_at"}`,
            [channelId, me, who.rows[0].name, who.rows[0].employee_id,
             body, Number.isFinite(replyTo) ? replyTo : null, clientMsgId]
        );

        let row = inserted.rows[0];
        let duplicate = false;
        if (!row) {
            // The insert was suppressed, so this send already landed.
            duplicate = true;
            const existing = await client.query(
                `SELECT ${MESSAGE_COLUMNS} FROM messages m
                  WHERE m.channel_id = $1 AND m.client_msg_id = $2`,
                [channelId, clientMsgId]
            );
            row = existing.rows[0];
        }
        let mentioned = [];
        if (row && !duplicate) {
            if (attachmentIds.length) {
                // Only files this person uploaded, into this channel, and not
                // already claimed by another message. Without those three
                // conditions an id guessed from a URL would attach somebody
                // else's file to your message.
                await client.query(
                    `UPDATE attachments SET message_seq = $1
                      WHERE id = ANY($2) AND channel_id = $3
                        AND uploaded_by = $4 AND message_seq IS NULL`,
                    [row.seq, attachmentIds, channelId, me]);
            }

            mentioned = await resolveMentions(client, {
                channelId, senderId: me, body, explicitIds: mentionIds,
            });
            if (mentioned.length) {
                await client.query(
                    `INSERT INTO mentions (message_seq, employee_id)
                     SELECT $1, UNNEST($2::VARCHAR[]) ON CONFLICT DO NOTHING`,
                    [row.seq, mentioned]);
                // A separate notification, because "14 unread" is a number
                // people learn to ignore and "Priya asked you something" is
                // not. That difference is the entire point of mentions.
                await client.query(
                    `INSERT INTO notifications (employee_id, type, message_seq, channel_id)
                     SELECT UNNEST($1::VARCHAR[]), 'MENTION', $2, $3`,
                    [mentioned, row.seq, channelId]);
            }
        }

        await client.query("COMMIT");
        // Released HERE, not in the finally, because enrich() below asks the
        // pool for connections of its own. Holding this one across that call
        // is the deadlock this controller already had once: with DB_POOL_MAX
        // sends in flight, none can obtain a second connection and none will
        // give up the one it has. The hundred-sender check caught it a second
        // time — 30 of 100 accepted — which is the argument for that test
        // existing at all.
        client.release();
        client = null;

        if (!row) return fail(res, 409, "Message could not be stored");

        const [shaped] = await enrich([toMessage(row)], me);
        return res.status(duplicate ? 200 : 201).json({
            success: true,
            duplicate,
            mentioned: mentioned.length,
            message: shaped,
        });
    } catch (err) {
        if (client) await client.query("ROLLBACK").catch(() => {});
        return serverError(res, req, err);
    } finally {
        if (client) client.release();
    }
};

/**
 * PATCH /api/chat/messages/:seq   body: { body }
 *
 * Editing is allowed for five minutes; deleting is not allowed at all.
 *
 * Every previous version is kept. An edit window without version history is
 * a delete with extra steps — write something, change it to "." half a minute
 * later, and the original is gone while the record still claims to be
 * complete. The employee sees only "(edited)"; the versions are for the
 * audited super-admin view.
 */
exports.editMessage = async (req, res) => {
    const me = req.employee?.employee_id;
    if (!me) return fail(res, 401, "Unauthenticated");

    const seq = Number.parseInt(req.params.seq, 10);
    if (!Number.isFinite(seq)) return fail(res, 400, "Invalid message");

    const body = String(req.body?.body ?? "").trim();
    if (!body) return fail(res, 400, "Message cannot be empty");
    if (body.length > MAX_BODY) {
        return fail(res, 400, `Message is too long — ${body.length} of ${MAX_BODY} characters`);
    }

    // The visibility check runs on the pool BEFORE a dedicated client is
    // taken — see the note in sendMessage. Holding one connection while
    // asking the pool for another is what exhausts it under load.
    let client;
    try {
        const locate = await pool.query(
            `SELECT channel_id FROM messages WHERE seq = $1 AND deleted_at IS NULL`, [seq]);
        if (locate.rows.length === 0) return fail(res, 404, "Message not found");

        // Visibility first, so that "not yours" and "does not exist" are the
        // same answer to somebody outside the channel.
        const channel = await loadVisibleChannel(pool, me, locate.rows[0].channel_id);
        if (!channel) return fail(res, 404, "Message not found");

        client = await pool.connect();
        await client.query("BEGIN");
        // Re-read under the row lock. The checks above were made against a
        // state that may have moved on; the ones that decide whether the
        // write happens are made here.
        const found = await client.query(
            `SELECT m.seq, m.channel_id, m.sender_id, m.body, m.edit_count, m.created_at,
                    t.is_archived,
                    (NOW() - m.created_at) > INTERVAL '${EDIT_WINDOW_MINUTES} minutes' AS too_old
               FROM messages m
               JOIN channels c ON c.id = m.channel_id
               JOIN teams t ON t.id = c.team_id
              WHERE m.seq = $1 AND m.deleted_at IS NULL
              FOR UPDATE OF m`,
            [seq]
        );
        if (found.rows.length === 0) {
            await client.query("ROLLBACK");
            return fail(res, 404, "Message not found");
        }
        const message = found.rows[0];

        if (message.sender_id !== me) {
            await client.query("ROLLBACK");
            return fail(res, 403, "You can only edit your own messages.");
        }
        if (message.is_archived) {
            await client.query("ROLLBACK");
            return fail(res, 409, "This team is archived — it is read-only.");
        }
        if (message.too_old) {
            await client.query("ROLLBACK");
            return fail(res, 409,
                `Messages can only be edited within ${EDIT_WINDOW_MINUTES} minutes of sending.`);
        }
        if (message.body === body) {
            await client.query("ROLLBACK");
            return res.json({ success: true, unchanged: true });
        }

        const who = await client.query(
            `SELECT COALESCE(NULLIF(full_name, ''), username) AS name
               FROM employees WHERE employee_id = $1`, [me]);

        const version = message.edit_count + 1;
        await client.query(
            `INSERT INTO message_edits (message_seq, version, old_body, edited_by, edited_name)
             VALUES ($1, $2, $3, $4, $5)`,
            [seq, version, message.body, me, who.rows[0]?.name || me]
        );
        const updated = await client.query(
            `UPDATE messages
                SET body = $1, edited_at = NOW(), edit_count = edit_count + 1
              WHERE seq = $2
              RETURNING ${"seq, channel_id, sender_id, sender_name, sender_employee_code, body, reply_to, created_at, edit_count, pinned_at"}`,
            [body, seq]
        );
        await client.query("COMMIT");

        return res.json({ success: true, message: toMessage(updated.rows[0]) });
    } catch (err) {
        if (client) await client.query("ROLLBACK").catch(() => {});
        return serverError(res, req, err);
    } finally {
        if (client) client.release();
    }
};

// ───────────────────────────────────────────────────────────────────────────
//  Read state, search, presence
// ───────────────────────────────────────────────────────────────────────────

/** POST /api/chat/channels/:id/read   body: { seq } */
exports.markRead = async (req, res) => {
    const me = req.employee?.employee_id;
    if (!me) return fail(res, 401, "Unauthenticated");

    const channelId = Number.parseInt(req.params.id, 10);
    const seq = Number.parseInt(req.body?.seq, 10);
    if (!Number.isFinite(channelId)) return fail(res, 400, "Invalid channel");
    if (!Number.isFinite(seq) || seq < 0) return fail(res, 400, "seq must be a number");

    try {
        const channel = await loadVisibleChannel(pool, me, channelId);
        if (!channel) return fail(res, 404, "Channel not found");

        // GREATEST, never a plain assignment. Two panels open, or a poll
        // arriving out of order, would otherwise walk the mark backwards and
        // resurrect messages the person has already read.
        await pool.query(
            `INSERT INTO message_reads (employee_id, channel_id, last_read_seq, last_read_at)
             VALUES ($1, $2, $3, NOW())
             ON CONFLICT (employee_id, channel_id) DO UPDATE
                SET last_read_seq = GREATEST(message_reads.last_read_seq, EXCLUDED.last_read_seq),
                    last_read_at  = NOW()`,
            [me, channelId, seq]
        );

        // An announcement is only "seen" once its channel has been read.
        await pool.query(
            `UPDATE notifications SET is_read = true
              WHERE employee_id = $1 AND channel_id = $2 AND NOT is_read
                AND (message_seq IS NULL OR message_seq <= $3)`,
            [me, channelId, seq]
        );

        return res.json({ success: true });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/**
 * GET /api/chat/search?q=&channel_id=
 *
 * Only across channels this person can see — the same rule as everything
 * else, from the same module, because a search that leaks is a search that
 * shows you a conversation you were kept out of.
 *
 * The query is built with prefix matching ("report:*" finds reports and
 * reporting) against a 'simple' tsvector. See the migration for why an
 * English configuration is the wrong choice for Hinglish: it discards "me",
 * "do" and "to" as stopwords, so searching for them silently returns nothing.
 */
exports.search = async (req, res) => {
    const me = req.employee?.employee_id;
    if (!me) return fail(res, 401, "Unauthenticated");

    const q = String(req.query.q ?? "").trim();
    if (q.length < 2) return fail(res, 400, "Search for at least two characters");
    if (q.length > 200) return fail(res, 400, "Search text is too long");

    const channelId = req.query.channel_id ? Number.parseInt(req.query.channel_id, 10) : null;
    if (req.query.channel_id && !Number.isFinite(channelId)) {
        return fail(res, 400, "Invalid channel");
    }

    // Built by hand rather than through websearch_to_tsquery so that every
    // term gets the :* suffix. Anything that is not a letter or digit is
    // dropped — it is a search box, not a query language, and & | ! ( ) in
    // to_tsquery raise a syntax error on input a person could easily type.
    const terms = q.split(/[^\p{L}\p{N}]+/u).filter(Boolean).slice(0, 10);
    if (terms.length === 0) return fail(res, 400, "Nothing to search for");
    const tsquery = terms.map((t) => `${t}:*`).join(" & ");

    try {
        const result = await pool.query(
            `SELECT ${MESSAGE_COLUMNS}, c.name AS channel_name, t.name AS team_name,
                    ts_headline('simple', m.body, to_tsquery('simple', $2),
                                'StartSel=<b>,StopSel=</b>,MaxWords=30,MinWords=10') AS excerpt
               FROM messages m
               JOIN channels c ON c.id = m.channel_id
               JOIN teams t ON t.id = c.team_id
              WHERE m.body_tsv @@ to_tsquery('simple', $2)
                AND m.deleted_at IS NULL
                AND ($3::INTEGER IS NULL OR m.channel_id = $3)
                AND ${visibleChannelSql("c", 1)}
              ORDER BY m.seq DESC
              LIMIT 100`,
            [me, tsquery, channelId]
        );

        return res.json({
            success: true,
            query: q,
            total: result.rows.length,
            results: result.rows.map((row) => ({
                ...toMessage(row),
                channel_name: row.channel_name,
                team_name:    row.team_name,
                excerpt:      row.excerpt,
            })),
        });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/**
 * GET /api/chat/channels/:id/members
 *
 * The member list, with presence — and this is the one place this product
 * beats the thing it is modelled on. Microsoft Teams shows "Available", which
 * means the application is running; it stays green while somebody is at
 * lunch. Idle state here is measured, so the panel can say "idle 22 min" and
 * be telling the truth.
 *
 * Statuses: ACTIVE, IDLE (with since), SHIFT_ENDED, OFFLINE.
 */
exports.getChannelMembers = async (req, res) => {
    const me = req.employee?.employee_id;
    if (!me) return fail(res, 401, "Unauthenticated");

    const channelId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(channelId)) return fail(res, 400, "Invalid channel");

    try {
        const channel = await loadVisibleChannel(pool, me, channelId);
        if (!channel) return fail(res, 404, "Channel not found");

        // A channel's audience is the team for General, and the explicit list
        // otherwise — the same split the visibility rule uses.
        const result = await pool.query(
            `WITH audience AS (
                 SELECT tm.employee_id
                   FROM team_members tm
                  WHERE tm.team_id = $1 AND $2::BOOLEAN
                 UNION
                 SELECT cm.employee_id
                   FROM channel_members cm
                  WHERE cm.channel_id = $3
             )
             SELECT e.employee_id,
                    COALESCE(NULLIF(e.full_name, ''), e.username) AS name,
                    e.designation, e.role, e.suspended,
                    s.last_seen,
                    (s.token IS NOT NULL
                     AND s.last_seen > NOW() - INTERVAL '3 minutes') AS live,
                    (SELECT a.activity FROM activity_logs a
                      WHERE a.employee_id = e.employee_id
                        AND (UPPER(a.activity) LIKE '%USER IDLE%'
                          OR UPPER(a.activity) LIKE '%USER ACTIVE%')
                      ORDER BY a.created_at DESC LIMIT 1) AS last_state,
                    (SELECT a.created_at FROM activity_logs a
                      WHERE a.employee_id = e.employee_id
                        AND (UPPER(a.activity) LIKE '%USER IDLE%'
                          OR UPPER(a.activity) LIKE '%USER ACTIVE%')
                      ORDER BY a.created_at DESC LIMIT 1) AS last_state_at
               FROM audience au
               JOIN employees e ON e.employee_id = au.employee_id
               LEFT JOIN active_sessions s ON s.employee_id = e.employee_id
              ORDER BY name`,
            [channel.team_id, channel.is_default, channelId]
        );

        const members = result.rows.map((row) => {
            let status = "OFFLINE";
            let idle_minutes = null;
            if (row.live) {
                const idle = String(row.last_state || "").toUpperCase().includes("USER IDLE");
                status = idle ? "IDLE" : "ACTIVE";
                if (idle && row.last_state_at) {
                    idle_minutes = Math.max(
                        0, Math.round((Date.now() - new Date(row.last_state_at)) / 60000));
                }
            }
            return {
                employee_id: row.employee_id,
                name:        row.name,
                designation: row.designation,
                role:        row.role,
                suspended:   row.suspended,
                status,
                idle_minutes,
                last_seen:   row.live ? null : row.last_seen,
                is_me:       row.employee_id === me,
            };
        });

        return res.json({ success: true, channel_id: channelId, members });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/** POST /api/chat/notifications/read   body: { ids?: [] }  — all when omitted */
exports.markNotificationsRead = async (req, res) => {
    const me = req.employee?.employee_id;
    if (!me) return fail(res, 401, "Unauthenticated");

    const ids = Array.isArray(req.body?.ids) ? req.body.ids.map(Number).filter(Number.isFinite) : null;
    try {
        await pool.query(
            `UPDATE notifications SET is_read = true
              WHERE employee_id = $1 AND NOT is_read
                AND ($2::BIGINT[] IS NULL OR id = ANY($2))`,
            [me, ids && ids.length ? ids : null]
        );
        return res.json({ success: true });
    } catch (err) {
        return serverError(res, req, err);
    }
};

// ───────────────────────────────────────────────────────────────────────────
//  Pinning
// ───────────────────────────────────────────────────────────────────────────

/**
 * POST /api/chat/messages/:seq/pin   body: { pinned }
 *
 * Any member of the channel may pin or unpin, and who did it is recorded.
 *
 * Restricting it to administrators was tempting and would be wrong: the
 * messages worth pinning — this week's deadline, the VPN address — are known
 * to the people in the channel and not to an administrator, so a permission
 * gate would mean nothing ever got pinned. `pinned_by` is kept because "why
 * is this at the top" is asked later, and an unattributed pin is a small
 * mystery nobody can resolve.
 */
exports.setPinned = async (req, res) => {
    const me = req.employee?.employee_id;
    if (!me) return fail(res, 401, "Unauthenticated");

    const seq = Number.parseInt(req.params.seq, 10);
    if (!Number.isFinite(seq)) return fail(res, 400, "Invalid message");
    const pinned = req.body?.pinned === true || req.body?.pinned === "true";

    try {
        const found = await pool.query(
            `SELECT channel_id FROM messages WHERE seq = $1 AND deleted_at IS NULL`, [seq]);
        if (found.rows.length === 0) return fail(res, 404, "Message not found");

        const channel = await loadVisibleChannel(pool, me, found.rows[0].channel_id);
        if (!channel) return fail(res, 404, "Message not found");
        if (channel.is_archived) {
            return fail(res, 409, `${channel.team_name} is archived — it is read-only.`);
        }

        // A channel with fifty pinned messages has none: the point of the
        // shelf is that it is short enough to read.
        if (pinned) {
            const count = await pool.query(
                `SELECT COUNT(*) AS n FROM messages
                  WHERE channel_id = $1 AND pinned_at IS NOT NULL`, [channel.id]);
            if (Number(count.rows[0].n) >= MAX_PINNED) {
                return fail(res, 409,
                    `A channel can have ${MAX_PINNED} pinned messages. Unpin one first.`);
            }
        }

        await pool.query(
            `UPDATE messages
                SET pinned_at = CASE WHEN $2 THEN clock_timestamp() ELSE NULL END,
                    pinned_by = CASE WHEN $2 THEN $3 ELSE NULL END
              WHERE seq = $1`,
            [seq, pinned, me]);

        return res.json({ success: true, pinned });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/** GET /api/chat/channels/:id/pinned */
exports.getPinned = async (req, res) => {
    const me = req.employee?.employee_id;
    if (!me) return fail(res, 401, "Unauthenticated");

    const channelId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(channelId)) return fail(res, 400, "Invalid channel");

    try {
        const channel = await loadVisibleChannel(pool, me, channelId);
        if (!channel) return fail(res, 404, "Channel not found");

        const result = await pool.query(
            `SELECT ${MESSAGE_COLUMNS},
                    COALESCE(NULLIF(pe.full_name, ''), pe.username) AS pinned_by_name
               FROM messages m
               LEFT JOIN employees pe ON pe.employee_id = m.pinned_by
              WHERE m.channel_id = $1 AND m.pinned_at IS NOT NULL
                AND m.deleted_at IS NULL
              ORDER BY m.pinned_at DESC`,
            [channelId]);

        const shaped = await enrich(result.rows.map(toMessage), me);
        shaped.forEach((message, index) => {
            message.pinned_by_name = result.rows[index].pinned_by_name;
        });
        return res.json({ success: true, channel_id: channelId, messages: shaped });
    } catch (err) {
        return serverError(res, req, err);
    }
};

// ───────────────────────────────────────────────────────────────────────────
//  Attachments
// ───────────────────────────────────────────────────────────────────────────

/**
 * POST /api/chat/channels/:id/attachments   (multipart, field "file")
 *
 * The bytes arrive already encrypted — the client does that before sending,
 * exactly as it does for screenshots — so what is stored here cannot be read
 * from the disk it sits on.
 *
 * The file goes up BEFORE the message that carries it. Sending an empty
 * message first and attaching to it afterwards would leave a blank line in
 * the conversation for the length of the upload, and permanently if the
 * upload then failed. So an attachment starts with no message_seq and is
 * claimed when the message is sent; anything never claimed is swept up by
 * purge_old_data.
 */
exports.uploadAttachment = async (req, res) => {
    const me = req.employee?.employee_id;
    if (!me) return fail(res, 401, "Unauthenticated");

    const channelId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(channelId)) return fail(res, 400, "Invalid channel");
    if (!req.file) return fail(res, 400, "No file uploaded");

    const cleanup = () => {
        // A rejected upload has already been written to disk by multer. Left
        // there it is a file nothing references and nothing will ever delete.
        try { fs.unlinkSync(req.file.path); } catch (_) {}
    };

    try {
        const channel = await loadVisibleChannel(pool, me, channelId);
        if (!channel) { cleanup(); return fail(res, 404, "Channel not found"); }
        if (channel.is_archived) {
            cleanup();
            return fail(res, 409, `${channel.team_name} is archived — it is read-only.`);
        }
        if (channel.type === "ANNOUNCEMENT") {
            cleanup();
            return fail(res, 403,
                "This is an announcement channel — only administrators can post.");
        }

        // The name shown in the panel is what the person called it. It is
        // never used as a path — `stored_name` is generated by multer and is
        // the only thing that touches the filesystem.
        const displayName = String(req.body?.file_name || req.file.originalname || "file")
            .slice(0, 200);

        const inserted = await pool.query(
            `INSERT INTO attachments
                 (channel_id, file_name, stored_name, mime_type, size_bytes, uploaded_by)
             VALUES ($1, $2, $3, $4, $5, $6)
             RETURNING id, file_name, size_bytes`,
            [channelId, displayName, req.file.filename,
             String(req.body?.mime_type || "application/octet-stream").slice(0, 120),
             req.file.size, me]);

        return res.status(201).json({
            success: true,
            attachment: {
                id: Number(inserted.rows[0].id),
                file_name: inserted.rows[0].file_name,
                size_bytes: Number(inserted.rows[0].size_bytes),
            },
        });
    } catch (err) {
        cleanup();
        return serverError(res, req, err);
    }
};

/**
 * GET /api/chat/attachments/:id
 *
 * The same visibility rule as everything else, applied to the channel the
 * file was posted in. Without this an id counted upwards in a URL would walk
 * every file in the company.
 */
exports.downloadAttachment = async (req, res) => {
    const me = req.employee?.employee_id;
    if (!me) return fail(res, 401, "Unauthenticated");

    const id = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(id)) return fail(res, 400, "Invalid attachment");

    try {
        const result = await pool.query(
            `SELECT a.stored_name, a.file_name, a.mime_type, a.channel_id
               FROM attachments a
               JOIN channels c ON c.id = a.channel_id
              WHERE a.id = $2 AND a.message_seq IS NOT NULL
                AND ${visibleChannelSql("c", 1)}`,
            [me, id]);
        if (result.rows.length === 0) return fail(res, 404, "File not found");

        const file = result.rows[0];
        const directory = attachmentDir();
        // stored_name comes from the database rather than the request, but it
        // is still resolved and checked: the screenshot upload had a path
        // traversal of exactly this shape, and the cost of the check is
        // nothing next to being wrong about it.
        const full = path.resolve(directory, path.basename(file.stored_name));
        if (!full.startsWith(directory + path.sep)) {
            return fail(res, 400, "Invalid file");
        }
        if (!fs.existsSync(full)) return fail(res, 404, "File is no longer on disk");

        res.setHeader("Content-Type", file.mime_type || "application/octet-stream");
        res.setHeader("Content-Disposition",
            `attachment; filename="${file.file_name.replace(/["\r\n]/g, "")}"`);
        return fs.createReadStream(full).pipe(res);
    } catch (err) {
        return serverError(res, req, err);
    }
};

/** Where chat files live. Beside the screenshots, not among them. */
function attachmentDir() {
    const base = process.env.CHAT_UPLOAD_DIR
        ? path.resolve(process.env.CHAT_UPLOAD_DIR)
        : path.resolve(__dirname, "../uploads/chat");
    if (!fs.existsSync(base)) fs.mkdirSync(base, { recursive: true });
    return base;
}
exports.attachmentDir = attachmentDir;

exports.SEND_LOCK = SEND_LOCK;
exports.MAX_BODY = MAX_BODY;
exports.EDIT_WINDOW_MINUTES = EDIT_WINDOW_MINUTES;
exports.RATE_LIMIT_PER_MINUTE = RATE_LIMIT_PER_MINUTE;
exports.MAX_PINNED = MAX_PINNED;
exports.MAX_ATTACHMENT_BYTES = MAX_ATTACHMENT_BYTES;
