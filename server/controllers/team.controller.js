/**
 * Teams, channels and membership — the administrative half of chat.
 *
 * What the employee panel calls lives in chat.controller. This is what the
 * admin panel calls, plus the one route by which a super admin may read a
 * conversation.
 *
 * ON THAT LAST ROUTE. Nothing else in this system reads other people's
 * messages, and it would be easy to make it a quiet convenience. It is
 * deliberately not one: a purpose has to be given, it is recorded in a table
 * that is never purged, and a line is written to the audit log as well. That
 * is a protection for the person running this, not an obstacle — the question
 * "why were you reading my chat" arrives long after the answer has been
 * forgotten, and a written one is worth having.
 *
 * WHO MAY DO WHAT
 *   admin        — create and archive teams, create channels, manage members,
 *                  post announcements. Cannot read conversations.
 *   super admin  — all of the above, and may read conversations with a
 *                  recorded purpose.
 *
 * An admin managing another admin's team is allowed on purpose: with one
 * person away, work should not stop for want of a permission.
 */

const pool = require("../config/db");
const { teamWideSql } = require("../utils/chat_access");

const PURPOSES = [
    "HR_INVESTIGATION",
    "COMPLAINT",
    "LEGAL",
    "COMPLIANCE",
    "EMPLOYEE_REQUEST",
    "OTHER",
];

const DEFAULT_CHANNEL = "General";
const MAX_ANNOUNCEMENT = 2000;

const fail = (res, status, message) => res.status(status).json({ success: false, message });

function serverError(res, req, err) {
    console.error("[500]", req.method, req.originalUrl, err.message);
    return res.status(500).json({ success: false, message: "Internal server error" });
}

const actorId = (req) => req.employee?.employee_id || null;

async function audit(employeeId, line) {
    await pool.query(
        `INSERT INTO activity_logs (employee_id, activity) VALUES ($1, $2)`,
        [employeeId, line]
    ).catch(() => {});
}

async function actorName(employeeId) {
    const r = await pool.query(
        `SELECT COALESCE(NULLIF(full_name, ''), username) AS name
           FROM employees WHERE employee_id = $1`, [employeeId]);
    return r.rows[0]?.name || employeeId;
}

// ───────────────────────────────────────────────────────────────────────────
//  Teams
// ───────────────────────────────────────────────────────────────────────────

/** GET /api/admin/teams */
exports.listTeams = async (req, res) => {
    try {
        const result = await pool.query(
            `SELECT t.id, t.name, t.description, t.created_at, t.created_by,
                    t.is_archived, t.archived_at, t.archived_by, t.archived_reason,
                    COALESCE(NULLIF(ce.full_name, ''), ce.username) AS created_by_name,
                    (SELECT COUNT(*) FROM team_members tm WHERE tm.team_id = t.id)
                        AS member_count,
                    (SELECT COUNT(*) FROM channels c WHERE c.team_id = t.id)
                        AS channel_count,
                    (SELECT COUNT(*) FROM messages m
                       JOIN channels c ON c.id = m.channel_id
                      WHERE c.team_id = t.id) AS message_count
               FROM teams t
               LEFT JOIN employees ce ON ce.employee_id = t.created_by
              ORDER BY t.is_archived, LOWER(t.name)`
        );
        return res.json({
            success: true,
            teams: result.rows.map((r) => ({
                ...r,
                member_count:  Number(r.member_count),
                channel_count: Number(r.channel_count),
                message_count: Number(r.message_count),
            })),
        });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/**
 * POST /api/admin/teams   body: { name, description?, members?: [] }
 *
 * A General channel is created with the team, in the same transaction. A team
 * with no channel is a team nobody can post to, and leaving that state
 * reachable means somebody eventually finds it.
 */
exports.createTeam = async (req, res) => {
    const name = String(req.body?.name ?? "").trim();
    const description = String(req.body?.description ?? "").trim() || null;
    const members = Array.isArray(req.body?.members) ? req.body.members : [];

    if (!name) return fail(res, 400, "Team name is required");
    if (name.length > 120) return fail(res, 400, "Team name is too long");

    const client = await pool.connect();
    try {
        await client.query("BEGIN");
        const team = await client.query(
            `INSERT INTO teams (name, description, created_by) VALUES ($1, $2, $3)
             RETURNING id, name`,
            [name, description, actorId(req)]
        );
        const teamId = team.rows[0].id;

        await client.query(
            `INSERT INTO channels (team_id, name, type, is_default, created_by)
             VALUES ($1, $2, 'STANDARD', true, $3)`,
            [teamId, DEFAULT_CHANNEL, actorId(req)]
        );

        let added = 0;
        if (members.length) {
            const valid = await client.query(
                `SELECT employee_id FROM employees WHERE employee_id = ANY($1)`,
                [members.map(String)]
            );
            for (const row of valid.rows) {
                await client.query(
                    `INSERT INTO team_members (team_id, employee_id) VALUES ($1, $2)
                     ON CONFLICT DO NOTHING`,
                    [teamId, row.employee_id]
                );
                added += 1;
            }
        }
        await client.query("COMMIT");

        await audit(actorId(req), `TEAM CREATED : ${name} (${added} member(s))`);
        return res.status(201).json({
            success: true,
            team: { id: teamId, name, description, member_count: added },
            message: `${name} created with a ${DEFAULT_CHANNEL} channel`,
        });
    } catch (err) {
        await client.query("ROLLBACK").catch(() => {});
        if (err.code === "23505") return fail(res, 409, `A team called ${name} already exists`);
        return serverError(res, req, err);
    } finally {
        client.release();
    }
};

/** GET /api/admin/teams/:id */
exports.getTeam = async (req, res) => {
    const teamId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(teamId)) return fail(res, 400, "Invalid team");

    try {
        const team = await pool.query(
            `SELECT id, name, description, created_at, is_archived,
                    archived_at, archived_by, archived_reason
               FROM teams WHERE id = $1`, [teamId]);
        if (team.rows.length === 0) return fail(res, 404, "Team not found");

        const channels = await pool.query(
            `SELECT c.id, c.name, c.description, c.type, c.is_default, c.is_private,
                    c.created_at,
                    (SELECT COUNT(*) FROM messages m WHERE m.channel_id = c.id)
                        AS message_count,
                    (SELECT COUNT(*) FROM channel_members cm WHERE cm.channel_id = c.id)
                        AS member_count
               FROM channels c
              WHERE c.team_id = $1
              ORDER BY c.is_default DESC, LOWER(c.name)`, [teamId]);

        // Super admins appear in every team without a team_members row, so
        // the console shows the same membership the employees see.
        const members = await pool.query(
            `SELECT e.employee_id,
                    COALESCE(NULLIF(e.full_name, ''), e.username) AS name,
                    e.username, e.role, e.designation, e.suspended, tm.joined_at,
                    ARRAY(SELECT c.id FROM channel_members cm
                            JOIN channels c ON c.id = cm.channel_id
                           WHERE cm.employee_id = e.employee_id AND c.team_id = $1)
                        AS channel_ids
               FROM employees e
               LEFT JOIN team_members tm
                      ON tm.employee_id = e.employee_id AND tm.team_id = $1
              WHERE tm.employee_id IS NOT NULL OR e.role = 'super_admin'
              ORDER BY name`, [teamId]);

        return res.json({
            success: true,
            team: team.rows[0],
            channels: channels.rows.map((c) => ({
                ...c,
                message_count: Number(c.message_count),
                member_count:  Number(c.member_count),
            })),
            members: members.rows,
        });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/** PATCH /api/admin/teams/:id   body: { name?, description? } */
exports.updateTeam = async (req, res) => {
    const teamId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(teamId)) return fail(res, 400, "Invalid team");

    const name = req.body?.name === undefined ? null : String(req.body.name).trim();
    const description = req.body?.description === undefined
        ? undefined : (String(req.body.description).trim() || null);

    if (name !== null && !name) return fail(res, 400, "Team name cannot be empty");
    if (name && name.length > 120) return fail(res, 400, "Team name is too long");

    try {
        const result = await pool.query(
            `UPDATE teams
                SET name = COALESCE($2, name),
                    description = CASE WHEN $4 THEN $3 ELSE description END
              WHERE id = $1
              RETURNING id, name, description`,
            [teamId, name, description === undefined ? null : description,
             description !== undefined]
        );
        if (result.rows.length === 0) return fail(res, 404, "Team not found");
        return res.json({ success: true, team: result.rows[0] });
    } catch (err) {
        if (err.code === "23505") return fail(res, 409, "A team with that name already exists");
        return serverError(res, req, err);
    }
};

/**
 * POST /api/admin/teams/:id/archive   body: { archived, reason? }
 *
 * There is no delete. Removing a team would take every conversation in it,
 * which is the opposite of keeping chat indefinitely. Archiving closes it to
 * new messages and leaves it readable and searchable.
 */
exports.archiveTeam = async (req, res) => {
    const teamId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(teamId)) return fail(res, 400, "Invalid team");

    const archived = req.body?.archived === true || req.body?.archived === "true";
    const reason = String(req.body?.reason ?? "").trim() || null;

    // Archiving without a reason is the state that is unexplainable later —
    // the same argument as suspended_by on employees.
    if (archived && !reason) {
        return fail(res, 400, "A reason is required when archiving a team.");
    }

    try {
        const current = await pool.query(`SELECT name, is_archived FROM teams WHERE id = $1`,
            [teamId]);
        if (current.rows.length === 0) return fail(res, 404, "Team not found");
        if (current.rows[0].is_archived === archived) {
            return res.json({
                success: true, archived,
                message: `${current.rows[0].name} is already ${archived ? "archived" : "active"}`,
            });
        }

        await pool.query(
            `UPDATE teams
                SET is_archived     = $2,
                    archived_at     = CASE WHEN $2 THEN NOW() ELSE NULL END,
                    archived_by     = CASE WHEN $2 THEN $3 ELSE NULL END,
                    archived_reason = CASE WHEN $2 THEN $4 ELSE NULL END
              WHERE id = $1`,
            [teamId, archived, actorId(req), reason]
        );

        await audit(actorId(req),
            `${archived ? "TEAM ARCHIVED" : "TEAM RESTORED"} : ${current.rows[0].name}` +
            (reason ? ` — ${reason}` : ""));

        return res.json({
            success: true, archived,
            message: archived
                ? `${current.rows[0].name} is archived and read-only`
                : `${current.rows[0].name} is active again`,
        });
    } catch (err) {
        return serverError(res, req, err);
    }
};

// ───────────────────────────────────────────────────────────────────────────
//  Channels
// ───────────────────────────────────────────────────────────────────────────

/**
 * POST /api/admin/teams/:id/channels
 * body: { name, description?, type?, is_private?, members?: [] }
 */
exports.createChannel = async (req, res) => {
    const teamId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(teamId)) return fail(res, 400, "Invalid team");

    const name = String(req.body?.name ?? "").trim();
    const description = String(req.body?.description ?? "").trim() || null;
    const type = String(req.body?.type ?? "STANDARD").toUpperCase();
    const isPrivate = req.body?.is_private === true || req.body?.is_private === "true";
    const members = Array.isArray(req.body?.members) ? req.body.members.map(String) : [];

    if (!name) return fail(res, 400, "Channel name is required");
    if (name.length > 120) return fail(res, 400, "Channel name is too long");
    if (!["STANDARD", "ANNOUNCEMENT"].includes(type)) {
        return fail(res, 400, "Channel type must be STANDARD or ANNOUNCEMENT");
    }

    const client = await pool.connect();
    try {
        const team = await client.query(`SELECT name, is_archived FROM teams WHERE id = $1`,
            [teamId]);
        if (team.rows.length === 0) return fail(res, 404, "Team not found");
        if (team.rows[0].is_archived) {
            return fail(res, 409, "This team is archived — it is read-only.");
        }

        await client.query("BEGIN");
        const channel = await client.query(
            `INSERT INTO channels (team_id, name, description, type, is_private, created_by)
             VALUES ($1, $2, $3, $4::channel_type, $5, $6)
             RETURNING id, name, type, is_private`,
            [teamId, name, description, type, isPrivate, actorId(req)]
        );
        const channelId = channel.rows[0].id;

        // Only members of the team may be added — otherwise the visibility
        // rule, which requires both, would silently grant nothing and the
        // channel would look broken rather than misconfigured.
        let added = 0;
        if (members.length) {
            const valid = await client.query(
                `SELECT tm.employee_id FROM team_members tm
                  WHERE tm.team_id = $1 AND tm.employee_id = ANY($2)`,
                [teamId, members]
            );
            for (const row of valid.rows) {
                await client.query(
                    `INSERT INTO channel_members (channel_id, employee_id) VALUES ($1, $2)
                     ON CONFLICT DO NOTHING`, [channelId, row.employee_id]);
                added += 1;
            }
        }
        await client.query("COMMIT");

        await audit(actorId(req),
            `CHANNEL CREATED : ${team.rows[0].name} / ${name} (${type})`);

        return res.status(201).json({
            success: true,
            channel: { ...channel.rows[0], team_id: teamId, member_count: added },
        });
    } catch (err) {
        await client.query("ROLLBACK").catch(() => {});
        if (err.code === "23505") {
            return fail(res, 409, `That team already has a channel called ${name}`);
        }
        return serverError(res, req, err);
    } finally {
        client.release();
    }
};

/** PATCH /api/admin/channels/:id   body: { name?, description? } */
exports.updateChannel = async (req, res) => {
    const channelId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(channelId)) return fail(res, 400, "Invalid channel");

    const name = req.body?.name === undefined ? null : String(req.body.name).trim();
    const description = req.body?.description === undefined
        ? undefined : (String(req.body.description).trim() || null);

    if (name !== null && !name) return fail(res, 400, "Channel name cannot be empty");

    try {
        const current = await pool.query(
            `SELECT is_default FROM channels WHERE id = $1`, [channelId]);
        if (current.rows.length === 0) return fail(res, 404, "Channel not found");
        // Renaming General would leave a team with no channel by that name
        // while every employee still expects one there.
        if (current.rows[0].is_default && name) {
            return fail(res, 409, `The ${DEFAULT_CHANNEL} channel cannot be renamed.`);
        }

        const result = await pool.query(
            `UPDATE channels
                SET name = COALESCE($2, name),
                    description = CASE WHEN $4 THEN $3 ELSE description END
              WHERE id = $1
              RETURNING id, name, description, type, is_default, is_private`,
            [channelId, name, description === undefined ? null : description,
             description !== undefined]
        );
        return res.json({ success: true, channel: result.rows[0] });
    } catch (err) {
        if (err.code === "23505") {
            return fail(res, 409, "That team already has a channel with that name");
        }
        return serverError(res, req, err);
    }
};

/**
 * POST /api/admin/channels/:id/announce   body: { body }
 *
 * The one way anything is written into an ANNOUNCEMENT channel. Everyone who
 * can see the channel gets a notification row, because an announcement that
 * only raises an unread count is one people scroll past.
 */
exports.postAnnouncement = async (req, res) => {
    const channelId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(channelId)) return fail(res, 400, "Invalid channel");

    const body = String(req.body?.body ?? "").trim();
    if (!body) return fail(res, 400, "Announcement cannot be empty");
    if (body.length > MAX_ANNOUNCEMENT) {
        return fail(res, 400, `Announcement is too long — ${body.length} of ${MAX_ANNOUNCEMENT}`);
    }

    // Everything that can be answered from the pool is answered before a
    // dedicated client is taken. Holding one while asking the pool for
    // another exhausts it under concurrency — see the note in
    // chat.controller's sendMessage.
    let client;
    try {
        const channel = await pool.query(
            `SELECT c.id, c.team_id, c.name, c.type, c.is_default, t.name AS team_name,
                    t.is_archived, ${teamWideSql("c")} AS team_wide
               FROM channels c JOIN teams t ON t.id = c.team_id
              WHERE c.id = $1`, [channelId]);
        if (channel.rows.length === 0) return fail(res, 404, "Channel not found");
        const ch = channel.rows[0];
        if (ch.is_archived) return fail(res, 409, "This team is archived — it is read-only.");
        if (ch.type !== "ANNOUNCEMENT") {
            return fail(res, 400, "That is not an announcement channel.");
        }

        const name = await actorName(actorId(req));

        client = await pool.connect();
        await client.query("BEGIN");
        // Same lock as chat.controller — announcements share the sequence with
        // everything else, so they have to share its ordering guarantee too.
        await client.query("SELECT pg_advisory_xact_lock($1)", [0x45545301]);
        const inserted = await client.query(
            `INSERT INTO messages (channel_id, sender_id, sender_name,
                                   sender_employee_code, body)
             VALUES ($1, $2, $3, $2, $4)
             RETURNING seq, created_at`,
            [channelId, actorId(req), name, body]
        );
        const seq = inserted.rows[0].seq;

        await client.query(
            `INSERT INTO notifications (employee_id, type, message_seq, channel_id)
             SELECT tm.employee_id, 'ANNOUNCEMENT', $1, $2
               FROM team_members tm
              WHERE tm.team_id = $3
                AND ($4::BOOLEAN
                     OR EXISTS (SELECT 1 FROM channel_members cm
                                 WHERE cm.channel_id = $2
                                   AND cm.employee_id = tm.employee_id))`,
            [seq, channelId, ch.team_id, ch.team_wide]
        );
        await client.query("COMMIT");

        await audit(actorId(req),
            `ANNOUNCEMENT POSTED : ${ch.team_name} / ${ch.name}`);

        return res.status(201).json({
            success: true,
            message: { seq: Number(seq), created_at: inserted.rows[0].created_at },
        });
    } catch (err) {
        if (client) await client.query("ROLLBACK").catch(() => {});
        return serverError(res, req, err);
    } finally {
        if (client) client.release();
    }
};

// ───────────────────────────────────────────────────────────────────────────
//  Membership
// ───────────────────────────────────────────────────────────────────────────

/** POST /api/admin/teams/:id/members   body: { employee_ids: [] } */
exports.addMembers = async (req, res) => {
    const teamId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(teamId)) return fail(res, 400, "Invalid team");

    const ids = Array.isArray(req.body?.employee_ids)
        ? req.body.employee_ids.map(String) : [];
    if (ids.length === 0) return fail(res, 400, "No employees given");

    try {
        const team = await pool.query(`SELECT name, is_archived FROM teams WHERE id = $1`,
            [teamId]);
        if (team.rows.length === 0) return fail(res, 404, "Team not found");
        if (team.rows[0].is_archived) {
            return fail(res, 409, "This team is archived — it is read-only.");
        }

        const result = await pool.query(
            `INSERT INTO team_members (team_id, employee_id)
             SELECT $1, e.employee_id FROM employees e WHERE e.employee_id = ANY($2)
             ON CONFLICT DO NOTHING
             RETURNING employee_id`,
            [teamId, ids]
        );

        await audit(actorId(req),
            `TEAM MEMBERS ADDED : ${team.rows[0].name} — ${result.rows.length} added`);

        return res.json({
            success: true,
            added: result.rows.map((r) => r.employee_id),
            message: `${result.rows.length} added to ${team.rows[0].name}`,
        });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/**
 * DELETE /api/admin/teams/:id/members/:employee_id
 *
 * Their messages stay. Removing somebody from a team is not a reason to take
 * their side of every conversation with them.
 */
exports.removeMember = async (req, res) => {
    const teamId = Number.parseInt(req.params.id, 10);
    const employeeId = String(req.params.employee_id || "");
    if (!Number.isFinite(teamId) || !employeeId) return fail(res, 400, "Invalid request");

    const client = await pool.connect();
    try {
        const team = await client.query(`SELECT name FROM teams WHERE id = $1`, [teamId]);
        if (team.rows.length === 0) return fail(res, 404, "Team not found");

        // A super admin is in every team by role, not by a row — so there is
        // nothing to delete, and letting the attempt look like it worked
        // would be worse than refusing it.
        const target = await client.query(
            `SELECT role FROM employees WHERE employee_id = $1`, [employeeId]);
        if (target.rows[0]?.role === "super_admin") {
            return fail(res, 403,
                "A super admin is in every team and cannot be removed from one.");
        }

        await client.query("BEGIN");
        const removed = await client.query(
            `DELETE FROM team_members WHERE team_id = $1 AND employee_id = $2
             RETURNING employee_id`, [teamId, employeeId]);
        // Channel grants inside a team they are no longer in would otherwise
        // sit there and take effect again if they were ever re-added.
        await client.query(
            `DELETE FROM channel_members cm USING channels c
              WHERE cm.channel_id = c.id AND c.team_id = $1 AND cm.employee_id = $2`,
            [teamId, employeeId]);
        await client.query("COMMIT");

        if (removed.rows.length === 0) return fail(res, 404, "They are not in that team");

        await audit(actorId(req),
            `TEAM MEMBER REMOVED : ${team.rows[0].name} — ${employeeId}`);
        return res.json({ success: true, message: `Removed from ${team.rows[0].name}` });
    } catch (err) {
        await client.query("ROLLBACK").catch(() => {});
        return serverError(res, req, err);
    } finally {
        client.release();
    }
};

/** POST /api/admin/channels/:id/members   body: { employee_ids: [] } */
exports.addChannelMembers = async (req, res) => {
    const channelId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(channelId)) return fail(res, 400, "Invalid channel");

    const ids = Array.isArray(req.body?.employee_ids)
        ? req.body.employee_ids.map(String) : [];
    if (ids.length === 0) return fail(res, 400, "No employees given");

    try {
        const channel = await pool.query(
            `SELECT c.id, c.team_id, c.name, c.is_default, t.is_archived,
                    ${teamWideSql("c")} AS team_wide
               FROM channels c JOIN teams t ON t.id = c.team_id WHERE c.id = $1`,
            [channelId]);
        if (channel.rows.length === 0) return fail(res, 404, "Channel not found");
        if (channel.rows[0].is_archived) {
            return fail(res, 409, "This team is archived — it is read-only.");
        }
        // Everyone in the team is already in General; rows here would be
        // meaningless and would suggest the list means something it does not.
        if (channel.rows[0].team_wide) {
            return fail(res, 400,
                `Everyone in the team can already see ${channel.rows[0].name}.`);
        }

        const result = await pool.query(
            `INSERT INTO channel_members (channel_id, employee_id)
             SELECT $1, tm.employee_id FROM team_members tm
              WHERE tm.team_id = $2 AND tm.employee_id = ANY($3)
             ON CONFLICT DO NOTHING
             RETURNING employee_id`,
            [channelId, channel.rows[0].team_id, ids]
        );

        const skipped = ids.length - result.rows.length;
        return res.json({
            success: true,
            added: result.rows.map((r) => r.employee_id),
            message: result.rows.length +
                ` added to ${channel.rows[0].name}` +
                (skipped > 0 ? ` — ${skipped} skipped (not in the team, or already added)` : ""),
        });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/** DELETE /api/admin/channels/:id/members/:employee_id */
exports.removeChannelMember = async (req, res) => {
    const channelId = Number.parseInt(req.params.id, 10);
    const employeeId = String(req.params.employee_id || "");
    if (!Number.isFinite(channelId) || !employeeId) return fail(res, 400, "Invalid request");

    try {
        const channel = await pool.query(
            `SELECT name, is_default, ${teamWideSql("channels")} AS team_wide
               FROM channels WHERE id = $1`, [channelId]);
        if (channel.rows.length === 0) return fail(res, 404, "Channel not found");
        if (channel.rows[0].team_wide) {
            return fail(res, 400,
                `Access to ${channel.rows[0].name} comes from team membership — remove them from the team instead.`);
        }

        const target = await pool.query(
            `SELECT role FROM employees WHERE employee_id = $1`, [employeeId]);
        if (target.rows[0]?.role === "super_admin") {
            return fail(res, 403,
                "A super admin can see every channel and cannot be removed from one.");
        }

        const removed = await pool.query(
            `DELETE FROM channel_members WHERE channel_id = $1 AND employee_id = $2
             RETURNING employee_id`, [channelId, employeeId]);
        if (removed.rows.length === 0) return fail(res, 404, "They are not in that channel");

        return res.json({ success: true, message: `Removed from ${channel.rows[0].name}` });
    } catch (err) {
        return serverError(res, req, err);
    }
};

// ───────────────────────────────────────────────────────────────────────────
//  Reading a conversation — super admin only, and on the record
// ───────────────────────────────────────────────────────────────────────────

/**
 * POST /api/admin/chat/view
 * body: { channel_id, purpose, reference_id?, note?, before?, limit? }
 *
 * A POST rather than a GET because it writes: the act of reading is itself
 * recorded. That also keeps the purpose out of the URL, where it would end up
 * in access logs and browser history.
 *
 * Every version of every edited message is included. The point of keeping
 * edit history is that this view can show it — otherwise somebody can put a
 * sentence in a channel, change it a minute later, and the only record of
 * what was actually said is one nobody can reach.
 */
exports.viewChannel = async (req, res) => {
    const channelId = Number.parseInt(req.body?.channel_id, 10);
    const purpose = String(req.body?.purpose ?? "").toUpperCase().trim();
    const referenceId = String(req.body?.reference_id ?? "").trim() || null;
    const note = String(req.body?.note ?? "").trim() || null;
    const before = req.body?.before ? Number.parseInt(req.body.before, 10) : null;
    const limit = Math.min(Math.max(Number.parseInt(req.body?.limit, 10) || 100, 1), 500);

    if (!Number.isFinite(channelId)) return fail(res, 400, "Invalid channel");
    if (!PURPOSES.includes(purpose)) {
        return fail(res, 400, `Purpose must be one of: ${PURPOSES.join(", ")}`);
    }
    // OTHER exists so the list can stay short, not so it can be a way around
    // giving a reason.
    if (purpose === "OTHER" && !note) {
        return fail(res, 400, "Describe the reason when the purpose is Other.");
    }
    if (purpose !== "OTHER" && purpose !== "EMPLOYEE_REQUEST" && !referenceId) {
        return fail(res, 400, "A reference is required — for example Complaint #214.");
    }

    try {
        const channel = await pool.query(
            `SELECT c.id, c.name, c.type, c.team_id, t.name AS team_name
               FROM channels c JOIN teams t ON t.id = c.team_id WHERE c.id = $1`,
            [channelId]);
        if (channel.rows.length === 0) return fail(res, 404, "Channel not found");
        const ch = channel.rows[0];

        const messages = await pool.query(
            `SELECT m.seq, m.channel_id, m.sender_id, m.sender_name,
                    m.sender_employee_code, m.body, m.reply_to, m.created_at,
                    m.edited_at, m.edit_count, m.deleted_at, m.deleted_by
               FROM messages m
              WHERE m.channel_id = $1
                AND ($2::BIGINT IS NULL OR m.seq < $2)
              ORDER BY m.seq DESC LIMIT $3`,
            [channelId, before, limit]);

        const rows = messages.rows.reverse();
        const edited = rows.filter((r) => r.edit_count > 0).map((r) => r.seq);
        let history = [];
        if (edited.length) {
            const versions = await pool.query(
                `SELECT message_seq, version, old_body, edited_name, edited_at
                   FROM message_edits WHERE message_seq = ANY($1)
                  ORDER BY message_seq, version`, [edited]);
            history = versions.rows;
        }

        const viewer = actorId(req);
        await pool.query(
            `INSERT INTO chat_access_log
                 (viewer_id, viewer_name, team_id, channel_id, team_name, channel_name,
                  purpose, reference_id, note)
             VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
            [viewer, await actorName(viewer), ch.team_id, channelId,
             ch.team_name, ch.name, purpose, referenceId, note]
        );
        // Also as one line in activity_logs, so it appears in the audit report
        // without that report needing to know this table exists.
        await audit(viewer,
            `CHAT VIEWED : ${ch.team_name} / ${ch.name} — ${purpose}` +
            (referenceId ? ` (${referenceId})` : ""));

        return res.json({
            success: true,
            channel: { id: ch.id, name: ch.name, type: ch.type, team_name: ch.team_name },
            messages: rows.map((r) => ({
                seq: Number(r.seq),
                sender_id: r.sender_id,
                sender_name: r.sender_name,
                former: r.sender_id === null,
                // The ORIGINAL text, deliberately, even for a message its
                // author has withdrawn. This is the whole reason deletion is a
                // flag rather than a DELETE: the channel shows a tombstone,
                // this recorded and purpose-bound view shows what was said.
                // Anything else would let an employee remove evidence from the
                // company record after the fact, which the owner of this
                // system ruled out before chat was built.
                body: r.body,
                deleted: Boolean(r.deleted_at),
                deleted_at: r.deleted_at || null,
                deleted_by: r.deleted_by || null,
                created_at: r.created_at,
                edited_at: r.edited_at,
                edit_count: r.edit_count,
                reply_to: r.reply_to === null ? null : Number(r.reply_to),
            })),
            edit_history: history.map((h) => ({
                message_seq: Number(h.message_seq),
                version: h.version,
                old_body: h.old_body,
                edited_name: h.edited_name,
                edited_at: h.edited_at,
            })),
            has_more: messages.rows.length === limit,
            recorded: true,
        });
    } catch (err) {
        return serverError(res, req, err);
    }
};

/** GET /api/admin/chat/access-log?from=&to= */
exports.getAccessLog = async (req, res) => {
    const from = req.query.from || null;
    const to = req.query.to || null;
    const DATE = /^\d{4}-\d{2}-\d{2}$/;
    if ((from && !DATE.test(from)) || (to && !DATE.test(to))) {
        return fail(res, 400, "Dates must be YYYY-MM-DD");
    }

    try {
        const result = await pool.query(
            `SELECT id, viewer_id, viewer_name, team_name, channel_name,
                    purpose, reference_id, note,
                    TO_CHAR((viewed_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata',
                            'YYYY-MM-DD HH24:MI') AS at
               FROM chat_access_log
              WHERE ($1::DATE IS NULL
                     OR DATE((viewed_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata') >= $1)
                AND ($2::DATE IS NULL
                     OR DATE((viewed_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata') <= $2)
              ORDER BY viewed_at DESC LIMIT 500`,
            [from, to]);

        const byPurpose = new Map();
        for (const row of result.rows) {
            byPurpose.set(row.purpose, (byPurpose.get(row.purpose) || 0) + 1);
        }

        return res.json({
            success: true,
            total: result.rows.length,
            entries: result.rows,
            by_purpose: [...byPurpose].map(([purpose, count]) => ({ purpose, count })),
        });
    } catch (err) {
        return serverError(res, req, err);
    }
};

exports.PURPOSES = PURPOSES;
exports.DEFAULT_CHANNEL = DEFAULT_CHANNEL;
