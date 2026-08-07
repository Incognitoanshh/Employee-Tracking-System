/**
 * One definition of "which channels may this person see", used everywhere.
 *
 * Every read path in chat needs this: listing teams, fetching history,
 * polling for new messages, searching, marking read, sending. Six places
 * asking the same question is six chances to answer it differently, and the
 * one that gets it wrong shows somebody another department's conversation.
 * That is the failure this module exists to prevent, so nothing outside it
 * should ever write its own membership test.
 *
 * THE RULE
 *   A channel is visible when the person is in its team, AND one of:
 *     - it is the team's default channel (General), or
 *     - it is an announcement channel that is not private, or
 *     - they have been added to it explicitly.
 *
 * Being added to a team therefore grants General, the announcements, and
 * nothing more. That is deliberately stricter than Microsoft Teams, where any
 * standard channel is visible to the whole team.
 *
 * ANNOUNCEMENTS ARE TEAM-WIDE ON PURPOSE. They exist to carry notices — a
 * maintenance window, a policy change, a holiday — to everybody. Requiring
 * each person to be added to one would mean an announcement channel with no
 * members is invisible to the entire team while still accepting posts, so an
 * administrator would write a notice, see it saved, and reach nobody. That is
 * a silent failure of exactly the kind this module exists to prevent, and it
 * was found by the announcement checks in test_chat rather than by reasoning.
 * A private announcement channel still works the explicit way.
 *
 * Administrators get no implicit access here. An admin who is not in the
 * team sees nothing, and a super admin reads conversations through the
 * audited route in team.controller — never through this.
 */

/**
 * SQL that is true for channels a given person may see.
 *
 * Returns a fragment to drop into a WHERE clause, given the alias of a
 * `channels` row already in scope.
 *
 * @param {string} channelAlias alias of the channels table in the query
 * @param {number|string} employee Either the $n carrying the employee_id, or
 *   a column expression such as `e.employee_id`. The second form is needed by
 *   mention resolution, which asks the question about a whole set of
 *   candidates at once rather than about one bound value — and getting that
 *   wrong would test the SENDER's access instead of the mentioned person's,
 *   quietly notifying people about channels they are not in.
 */
function visibleChannelSql(channelAlias = "c", employee = 1) {
    const c = channelAlias;
    const who = typeof employee === "number" ? `$${employee}` : employee;
    return `(
        EXISTS (SELECT 1 FROM team_members tm
                 WHERE tm.team_id = ${c}.team_id AND tm.employee_id = ${who})
        AND (
            ${teamWideSql(c)}
            OR EXISTS (SELECT 1 FROM channel_members cm
                        WHERE cm.channel_id = ${c}.id AND cm.employee_id = ${who})
        )
    )`;
}

/**
 * True for channels every member of the team can see without being added.
 *
 * Kept separate because two other places need the same test — deciding who
 * an announcement notifies, and refusing to add members to a channel where
 * membership would mean nothing — and they must not drift from the rule
 * above.
 */
function teamWideSql(channelAlias = "c") {
    return `(${channelAlias}.is_default
             OR (${channelAlias}.type = 'ANNOUNCEMENT' AND NOT ${channelAlias}.is_private))`;
}

/**
 * Is this channel one the whole team is in, without being added?
 *
 * The JavaScript twin of teamWideSql, for the places that already hold the
 * channel row and would otherwise re-derive the rule by hand — which is how
 * the member list came to be empty for announcement channels: it tested
 * `is_default` alone, so a channel the entire team can read listed nobody.
 */
function isTeamWide(channel) {
    return Boolean(channel.is_default
        || (channel.type === "ANNOUNCEMENT" && !channel.is_private));
}

/**
 * Can this employee see this one channel?
 *
 * Returns the channel row (with its team's name and archived state) or null.
 * Callers need those anyway — an archived team is readable but closed to new
 * messages, and an announcement channel is readable but closed to everyone
 * except administrators.
 */
async function loadVisibleChannel(pool, employeeId, channelId) {
    const result = await pool.query(
        `SELECT c.id, c.team_id, c.name, c.type, c.is_default, c.is_private,
                t.name AS team_name, t.is_archived
           FROM channels c
           JOIN teams t ON t.id = c.team_id
          WHERE c.id = $2 AND ${visibleChannelSql("c", 1)}`,
        [employeeId, channelId]
    );
    return result.rows[0] || null;
}

module.exports = { visibleChannelSql, teamWideSql, isTeamWide, loadVisibleChannel };
