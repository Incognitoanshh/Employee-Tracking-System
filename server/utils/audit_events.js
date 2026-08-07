/**
 * Which activity_logs rows are evidence, and which are noise.
 *
 * The table holds two very different things. Most of it is volume — idle
 * and active flips, sign-ins, scheduler chatter — useful for a day or a
 * week and worthless after a month. A small fraction records what an
 * administrator DID: reset somebody's password, delete somebody's
 * screenshots, change how long data is kept.
 *
 * A single retention period cannot serve both. Short enough to keep the
 * table small deletes the evidence; long enough to keep the evidence keeps
 * millions of idle flips. So the noise is purged on the short period and
 * these are kept on a long one.
 *
 * Matching is by prefix on `activity`, because that is the shape every
 * writer already uses ("PASSWORD RESET : by A001"). Adding a new
 * administrative action means adding its prefix here — and forgetting to
 * means it is treated as noise, which is why the list sits next to the
 * writers rather than inside the purge script.
 */

const AUDIT_PREFIXES = [
    "PASSWORD CHANGED",
    "PASSWORD RESET",
    "SCREENSHOTS DELETED",
    "RETENTION CHANGED",
    "ROLE CHANGED",
    "EMPLOYEE CREATED",
    "EMPLOYEE DELETED",
    "FORCE LOGOUT",
    "AUTOSTART DISABLED",
    // Teams and chat. CHAT VIEWED is the one that matters most here: it is
    // the record of a super admin reading somebody else's conversation, and
    // it must outlive the short retention period by a long way.
    "TEAM CREATED",
    "TEAM ARCHIVED",
    "TEAM RESTORED",
    "TEAM MEMBERS ADDED",
    "TEAM MEMBER REMOVED",
    "CHANNEL CREATED",
    "ANNOUNCEMENT POSTED",
    "CHAT VIEWED",
];

/** SQL that is true for rows worth keeping longer. */
function auditRowsSql(column = "activity") {
    const patterns = AUDIT_PREFIXES.map((p) => `'${p}%'`).join(", ");
    return `${column} LIKE ANY (ARRAY[${patterns}])`;
}

module.exports = { AUDIT_PREFIXES, auditRowsSql };
