/**
 * Teams and chat.
 *
 * The checks that earn their place here are the ones where a broken chat
 * looks exactly like a working one:
 *
 *   * VISIBILITY. Being in a team grants General and nothing else. A leak
 *     here does not throw an error or show a warning — it just quietly puts
 *     another department's conversation on somebody's screen, and nobody
 *     reports it because it looks like a feature.
 *
 *   * DELIVERY. A message can be committed and delivered to nobody, if two
 *     senders take their sequence numbers in one order and commit in the
 *     other. Nothing errors. The message is in the database and on no screen.
 *
 *   * RETRY. The offline queue resends until the server confirms. When the
 *     first attempt landed and only the reply was lost, a resend must not
 *     duplicate — and duplicates would appear only on bad connections, which
 *     is exactly where nobody is watching.
 *
 *   * EDIT HISTORY. Editing without keeping versions is deleting with extra
 *     steps. If the history is not written, everything still appears to work.
 *
 *   * THE AUDITED READ. A super admin reading a conversation must be
 *     recorded, and an ordinary admin must not be able to at all.
 *
 * Run:  node server/tests/test_chat.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const crypto = require("crypto");

const DB = `ets_chat_${process.pid}`;
const PORT = 8000 + ((process.pid + 457) % 1000);
const BASE = `http://127.0.0.1:${PORT}/api`;
const PASSWORD = "SuperSecret123";

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}

function psql(db, sql) {
    return execFileSync("psql", ["-d", db, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        { encoding: "utf8" }).trim();
}

async function api(method, route, { token, body } = {}) {
    const response = await fetch(`${BASE}${route}`, {
        method,
        headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        ...(body && method !== "GET" ? { body: JSON.stringify(body) } : {}),
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return { status: response.status, body: payload };
}

const login = async (u) =>
    (await api("POST", "/auth/login", { body: { username: u, password: PASSWORD } })).body.token;

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`Teams and chat (${DB})\n`);

    psql("postgres", `CREATE DATABASE ${DB}`);
    try {
        for (const file of [
            path.join(root, "ets.sql"),
            path.join(root, "server", "migrations", "2026_08_05_password_management.sql"),
            path.join(root, "server", "migrations", "2026_08_05_username_case_insensitive.sql"),
            path.join(root, "server", "migrations", "2026_08_06_single_session.sql"),
            path.join(root, "server", "migrations", "2026_08_06_suspend.sql"),
            path.join(root, "server", "migrations", "2026_08_07_teams_chat.sql"),
            path.join(root, "server", "migrations", "2026_08_07_chat_phase2.sql"),
        ]) {
            execFileSync("psql", ["-d", DB, "-v", "ON_ERROR_STOP=1", "-q", "-f", file],
                { stdio: "pipe" });
        }

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        const people = [
            ["SA001", "superadmin", "super_admin", "Owner Sahab"],
            ["A001",  "admin1",     "admin",       "Admin One"],
            ["A002",  "admin2",     "admin",       "Admin Two"],
            ["E001",  "emp1",       "employee",    "Rajesh Kumar"],
            ["E002",  "emp2",       "employee",    "Amit Sharma"],
            ["E003",  "emp3",       "employee",    "Priya Singh"],
            ["E004",  "emp4",       "employee",    "Outsider Bhai"],
        ];
        // Ten more for the concurrency check — the rate limiter caps one
        // person at twenty a minute, and a hundred concurrent sends from one
        // account would be testing the limiter, not the ordering.
        for (let i = 1; i <= 10; i += 1) {
            people.push([`C${String(i).padStart(3, "0")}`, `conc${i}`, "employee", `Sender ${i}`]);
        }
        psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name) VALUES ` +
            people.map(([id, u, r, n]) =>
                `('${id}','${u}','${hash}','${r}','${n}')`).join(","));

        process.env.DB_HOST = process.env.PGHOST || "127.0.0.1";
        process.env.DB_PORT = process.env.PGPORT || "5432";
        process.env.DB_NAME = DB;
        process.env.DB_USER = process.env.PGUSER || process.env.USER;
        process.env.DB_PASSWORD = process.env.PGPASSWORD || "unused-locally";
        process.env.JWT_SECRET = "test-secret-not-used-in-production";
        process.env.PORT = String(PORT);
        process.env.ENCRYPTION_KEY = "0".repeat(64);

        const { server, pool } = require(path.join(root, "server", "server.js"));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        const sa = await login("superadmin");
        const admin = await login("admin1");
        const e1 = await login("emp1");
        const e2 = await login("emp2");
        const e3 = await login("emp3");
        const e4 = await login("emp4");

        // ── creating a team ─────────────────────────────────────────────
        console.log("Teams and channels");
        let res = await api("POST", "/admin/teams", {
            token: admin,
            body: { name: "Development", description: "Product team",
                    members: ["E001", "E002", "E003"] },
        });
        check("an admin creates a team", res.status === 201, `status ${res.status}`);
        const devTeam = res.body.team.id;

        check("a General channel comes with it",
            psql(DB, `SELECT name FROM channels WHERE team_id=${devTeam} AND is_default`) === "General");
        check("and only one of them can ever exist",
            psql(DB, `SELECT COUNT(*) FROM channels WHERE team_id=${devTeam} AND is_default`) === "1");

        res = await api("POST", "/admin/teams", { token: admin, body: { name: "development" } });
        check("a second team with the same name is refused",
            res.status === 409, `status ${res.status}`);

        res = await api("POST", "/admin/teams", {
            token: sa, body: { name: "HR", members: ["E003"] } });
        const hrTeam = res.body.team.id;
        check("a super admin creates one too", res.status === 201, `status ${res.status}`);

        res = await api("POST", "/admin/teams", { token: e1, body: { name: "Nope" } });
        check("an employee cannot create a team", res.status === 403, `status ${res.status}`);

        // A second channel, which nobody is in yet.
        res = await api("POST", `/admin/teams/${devTeam}/channels`, {
            token: admin, body: { name: "Backend", members: ["E001"] } });
        check("an admin adds a channel", res.status === 201, `status ${res.status}`);
        const backend = res.body.channel.id;

        res = await api("POST", `/admin/teams/${devTeam}/channels`, {
            token: admin, body: { name: "backend" } });
        check("a duplicate channel name in one team is refused",
            res.status === 409, `status ${res.status}`);

        const general = Number(psql(DB,
            `SELECT id FROM channels WHERE team_id=${devTeam} AND is_default`));
        const hrGeneral = Number(psql(DB,
            `SELECT id FROM channels WHERE team_id=${hrTeam} AND is_default`));

        // ── visibility: the rule that matters most ──────────────────────
        console.log("\nWho can see what");
        res = await api("GET", "/chat/me/teams", { token: e2 });
        let dev = res.body.teams.find((t) => t.id === devTeam);
        check("a team member sees the team", !!dev, JSON.stringify(res.body.teams));
        check("and its General channel",
            dev.channels.some((c) => c.id === general));
        check("but NOT a channel they were not added to",
            !dev.channels.some((c) => c.id === backend),
            JSON.stringify(dev.channels.map((c) => c.name)));

        res = await api("GET", "/chat/me/teams", { token: e1 });
        dev = res.body.teams.find((t) => t.id === devTeam);
        check("the member who WAS added to it sees it",
            dev.channels.some((c) => c.id === backend));

        res = await api("GET", "/chat/me/teams", { token: e4 });
        check("somebody in no team sees nothing at all",
            (res.body.teams || []).length === 0, JSON.stringify(res.body.teams));

        res = await api("GET", "/chat/me/teams", { token: e1 });
        check("and cannot see another department's team",
            !res.body.teams.some((t) => t.id === hrTeam),
            JSON.stringify(res.body.teams.map((t) => t.name)));

        res = await api("GET", `/chat/channels/${backend}/messages`, { token: e2 });
        check("reading a channel you are not in gives 404, not 403",
            res.status === 404, `status ${res.status}`);
        check("so the reply does not confirm the channel exists",
            !/permission|allowed|forbidden/i.test(res.body.message || ""), res.body.message);

        res = await api("GET", `/chat/channels/${hrGeneral}/messages`, { token: e1 });
        check("another team's General is invisible too", res.status === 404, `status ${res.status}`);

        // An admin gets no implicit access — they are not in the team.
        res = await api("GET", `/chat/channels/${general}/messages`, { token: admin });
        check("an admin who is not in the team cannot read it either",
            res.status === 404, `status ${res.status}`);

        // ── sending and reading ─────────────────────────────────────────
        console.log("\nSending");
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "kal ka report bhej dena" } });
        check("a member sends a message", res.status === 201, `status ${res.status}`);
        check("it comes back with the sender's name",
            res.body.message.sender_name === "Rajesh Kumar", res.body.message.sender_name);
        const firstSeq = res.body.message.seq;

        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e4, body: { body: "let me in" } });
        check("an outsider cannot send", res.status === 404, `status ${res.status}`);

        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e2, body: { body: "" } });
        check("an empty message is refused", res.status === 400, `status ${res.status}`);

        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e2, body: { body: "x".repeat(2001) } });
        check("an over-long message is refused", res.status === 400, `status ${res.status}`);
        check("and the reply says by how much",
            /2001 of 2000/.test(res.body.message || ""), res.body.message);

        // ── retry must not duplicate ────────────────────────────────────
        console.log("\nThe offline queue");
        const clientMsgId = crypto.randomUUID();
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e2, body: { body: "sent once", client_msg_id: clientMsgId } });
        check("a queued message is accepted", res.status === 201, `status ${res.status}`);
        const queuedSeq = res.body.message.seq;

        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e2, body: { body: "sent once", client_msg_id: clientMsgId } });
        check("the retry is NOT stored a second time",
            res.status === 200 && res.body.duplicate === true,
            `status ${res.status} duplicate=${res.body.duplicate}`);
        check("and it returns the original's seq so the client can settle",
            res.body.message.seq === queuedSeq,
            `${res.body.message.seq} vs ${queuedSeq}`);
        check("only one row exists",
            psql(DB, `SELECT COUNT(*) FROM messages WHERE client_msg_id='${clientMsgId}'`) === "1");

        // Replaying the whole queue after a reconnect — every message resent.
        const replayIds = [crypto.randomUUID(), crypto.randomUUID(), crypto.randomUUID()];
        for (const id of replayIds) {
            await api("POST", `/chat/channels/${general}/messages`, {
                token: e3, body: { body: `queued ${id.slice(0, 8)}`, client_msg_id: id } });
        }
        for (const id of replayIds) {
            await api("POST", `/chat/channels/${general}/messages`, {
                token: e3, body: { body: `queued ${id.slice(0, 8)}`, client_msg_id: id } });
        }
        check("replaying an entire queue adds nothing the second time",
            psql(DB, `SELECT COUNT(*) FROM messages WHERE client_msg_id = ANY(ARRAY[${
                replayIds.map((i) => `'${i}'`).join(",")}]::uuid[])`) === "3");

        // ── delivery, under concurrency ─────────────────────────────────
        console.log("\nDelivery");
        res = await api("GET", "/chat/updates?since=0", { token: e2 });
        check("a fresh client gets a cursor and no backlog",
            res.status === 200 && res.body.messages.length === 0 && res.body.cursor > 0,
            JSON.stringify({ n: res.body.messages.length, cursor: res.body.cursor }));
        const cursorBefore = res.body.cursor;

        const concTokens = [];
        for (let i = 1; i <= 10; i += 1) concTokens.push(await login(`conc${i}`));
        await api("POST", `/admin/teams/${devTeam}/members`, {
            token: admin,
            body: { employee_ids: concTokens.map((_, i) => `C${String(i + 1).padStart(3, "0")}`) },
        });

        // A hundred at once, ten from each of ten people.
        const sends = [];
        for (let p = 0; p < 10; p += 1) {
            for (let m = 0; m < 10; m += 1) {
                sends.push(api("POST", `/chat/channels/${general}/messages`, {
                    token: concTokens[p], body: { body: `burst ${p}-${m}` } }));
            }
        }
        const results = await Promise.all(sends);
        const accepted = results.filter((r) => r.status === 201).length;
        check("a hundred concurrent sends are all accepted",
            accepted === 100, `${accepted} of 100`);

        res = await api("GET", `/chat/updates?since=${cursorBefore}`, { token: e2 });
        const delivered = res.body.messages.filter((m) => m.body.startsWith("burst ")).length;
        check("and a single poll delivers every one of them",
            delivered === 100, `${delivered} of 100 — a stored-but-undelivered message`);

        // NOT "the sequence has no holes" — a suppressed duplicate still
        // consumes a sequence value, so holes are legitimate and a client
        // polling with seq > cursor does not care about them. What must hold
        // is that seq order and commit order agree: if a later seq were
        // committed first, a client could advance past one that had not
        // landed yet and never be shown it.
        const inversions = psql(DB, `
            SELECT COUNT(*) FROM (
              SELECT created_at, LAG(created_at) OVER (ORDER BY seq) AS prev
                FROM messages
            ) s WHERE prev IS NOT NULL AND created_at < prev`);
        check("a later seq is never committed before an earlier one",
            inversions === "0", `${inversions} inversion(s)`);

        // ── unread ──────────────────────────────────────────────────────
        console.log("\nUnread");
        res = await api("GET", "/chat/me/teams", { token: e2 });
        dev = res.body.teams.find((t) => t.id === devTeam);
        let generalChannel = dev.channels.find((c) => c.id === general);
        check("unread counts what has arrived", generalChannel.unread > 100,
            String(generalChannel.unread));

        await api("POST", `/chat/channels/${general}/read`, {
            token: e2, body: { seq: generalChannel.last_seq } });
        res = await api("GET", "/chat/me/teams", { token: e2 });
        generalChannel = res.body.teams.find((t) => t.id === devTeam)
            .channels.find((c) => c.id === general);
        check("marking read clears it", generalChannel.unread === 0,
            String(generalChannel.unread));

        // Two panels open, or a poll arriving late, must not un-read things.
        await api("POST", `/chat/channels/${general}/read`, { token: e2, body: { seq: 1 } });
        check("a stale read mark cannot move the marker backwards",
            Number(psql(DB, `SELECT last_read_seq FROM message_reads
                              WHERE employee_id='E002' AND channel_id=${general}`)) > 1);

        res = await api("GET", "/chat/me/teams", { token: e1 });
        const e1General = res.body.teams.find((t) => t.id === devTeam)
            .channels.find((c) => c.id === general);
        await api("POST", `/chat/channels/${general}/read`, {
            token: e1, body: { seq: e1General.last_seq } });
        await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "one more" } });
        const mine = await api("GET", "/chat/me/teams", { token: e1 });
        check("your own message is not unread for you",
            mine.body.teams.find((t) => t.id === devTeam)
                .channels.find((c) => c.id === general).unread === 0,
            String(mine.body.teams.find((t) => t.id === devTeam)
                .channels.find((c) => c.id === general).unread));

        // ── editing, and its history ────────────────────────────────────
        console.log("\nEditing");
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "Hello sir" } });
        const editable = res.body.message.seq;

        res = await api("PATCH", `/chat/messages/${editable}`, {
            token: e1, body: { body: "Hello sir pls ignore" } });
        check("the sender edits their own message", res.status === 200, `status ${res.status}`);
        check("and it is marked edited", res.body.message.edited === true);

        await api("PATCH", `/chat/messages/${editable}`, { token: e1, body: { body: "Hello sir" } });
        check("every version is kept",
            psql(DB, `SELECT COUNT(*) FROM message_edits WHERE message_seq=${editable}`) === "2");
        check("including the original wording",
            psql(DB, `SELECT old_body FROM message_edits
                       WHERE message_seq=${editable} AND version=1`) === "Hello sir");
        check("and the wording it was changed to in between",
            psql(DB, `SELECT old_body FROM message_edits
                       WHERE message_seq=${editable} AND version=2`) === "Hello sir pls ignore");

        res = await api("PATCH", `/chat/messages/${editable}`, {
            token: e2, body: { body: "not mine" } });
        check("nobody can edit somebody else's message",
            res.status === 403, `status ${res.status}`);

        res = await api("PATCH", `/chat/messages/${editable}`, {
            token: e4, body: { body: "outsider" } });
        check("an outsider gets 404, not 403",
            res.status === 404, `status ${res.status}`);

        psql(DB, `UPDATE messages
                     SET created_at = (NOW() AT TIME ZONE 'UTC') - INTERVAL '10 minutes'
                   WHERE seq = ${editable}`);
        res = await api("PATCH", `/chat/messages/${editable}`, {
            token: e1, body: { body: "too late" } });
        check("the edit window closes after five minutes",
            res.status === 409, `status ${res.status}`);
        check("and the message is unchanged",
            psql(DB, `SELECT body FROM messages WHERE seq=${editable}`) === "Hello sir");

        res = await api("DELETE", `/chat/messages/${editable}`, { token: e1 });
        check("there is no route to delete a message at all",
            res.status === 404, `status ${res.status}`);

        // ── renaming a channel ──────────────────────────────────────────
        //  Reachable from the admin panel's Edit dialog. It had no test at
        //  all until that dialog existed, which is the usual reason a route
        //  goes untested: nothing was calling it.
        console.log("\nEditing a channel");
        res = await api("PATCH", `/admin/channels/${backend}`, {
            token: admin, body: { name: "Backend Services", description: "APIs" } });
        check("a channel can be renamed and described",
            res.status === 200 && res.body.channel.name === "Backend Services",
            JSON.stringify(res.body.channel));
        check("and the description is stored",
            psql(DB, `SELECT description FROM channels WHERE id=${backend}`) === "APIs");

        res = await api("PATCH", `/admin/channels/${general}`, {
            token: admin, body: { name: "Lobby" } });
        check("General cannot be renamed — employees are told every team has one",
            res.status === 409, `status ${res.status}`);
        check("and it keeps its name",
            psql(DB, `SELECT name FROM channels WHERE id=${general}`) === "General");

        res = await api("PATCH", `/admin/channels/${general}`, {
            token: admin, body: { description: "Everything else" } });
        check("but its description can still be changed",
            res.status === 200, `status ${res.status}`);

        res = await api("PATCH", `/admin/channels/${backend}`, {
            token: admin, body: { name: "general" } });
        check("two channels in one team cannot share a name",
            res.status === 409, `status ${res.status}`);

        res = await api("PATCH", `/admin/channels/${backend}`, {
            token: e2, body: { name: "mine now" } });
        check("an employee cannot rename anything", res.status === 403, `status ${res.status}`);

        res = await api("PATCH", "/admin/channels/999999", {
            token: admin, body: { name: "ghost" } });
        check("renaming a channel that does not exist gives 404",
            res.status === 404, `status ${res.status}`);

        // Put it back so later checks read the way they were written.
        await api("PATCH", `/admin/channels/${backend}`, {
            token: admin, body: { name: "Backend" } });

        // ── access taken away mid-conversation ──────────────────────────
        console.log("\nWhen access is taken away");
        res = await api("GET", `/chat/channels/${backend}/messages`, { token: e1 });
        check("somebody in the channel can read it", res.status === 200, `status ${res.status}`);

        res = await api("DELETE", `/admin/channels/${backend}/members/E001`,
            { token: admin });
        check("an admin removes them from that channel only",
            res.status === 200, `status ${res.status}`);
        check("they are still in the team",
            psql(DB, `SELECT COUNT(*) FROM team_members
                       WHERE team_id=${devTeam} AND employee_id='E001'`) === "1");

        res = await api("GET", `/chat/channels/${backend}/messages`, { token: e1 });
        check("but the channel is immediately unreadable", res.status === 404,
            `status ${res.status}`);
        res = await api("POST", `/chat/channels/${backend}/messages`, {
            token: e1, body: { body: "still here?" } });
        check("and unwritable", res.status === 404, `status ${res.status}`);

        res = await api("GET", "/chat/me/teams", { token: e1 });
        dev = res.body.teams.find((t) => t.id === devTeam);
        check("it disappears from their channel list",
            !dev.channels.some((c) => c.id === backend),
            JSON.stringify(dev.channels.map((c) => c.name)));
        check("while General is untouched",
            dev.channels.some((c) => c.id === general));

        await api("POST", `/admin/channels/${backend}/members`, {
            token: admin, body: { employee_ids: ["E001"] } });
        res = await api("GET", `/chat/channels/${backend}/messages`, { token: e1 });
        check("adding them back restores it", res.status === 200, `status ${res.status}`);

        // ── announcements ───────────────────────────────────────────────
        console.log("\nAnnouncements");
        res = await api("POST", `/admin/teams/${devTeam}/channels`, {
            token: admin, body: { name: "Company Updates", type: "ANNOUNCEMENT" } });
        check("an announcement channel is created", res.status === 201, `status ${res.status}`);
        const announce = res.body.channel.id;

        res = await api("POST", `/chat/channels/${announce}/messages`, {
            token: e1, body: { body: "can I post here?" } });
        check("an employee cannot post to it", res.status === 403, `status ${res.status}`);
        check("and is told why",
            /only administrators/i.test(res.body.message || ""), res.body.message);

        res = await api("POST", `/admin/channels/${announce}/announce`, {
            token: admin, body: { body: "Maintenance tonight 11pm" } });
        check("an admin posts an announcement", res.status === 201, `status ${res.status}`);

        check("everyone in the team is notified individually",
            Number(psql(DB, `SELECT COUNT(*) FROM notifications
                              WHERE type='ANNOUNCEMENT' AND channel_id=${announce}`)) >= 3);

        res = await api("GET", "/chat/me/teams", { token: e2 });
        check("and the employee sees an unread notification",
            res.body.notifications_unread >= 1, String(res.body.notifications_unread));

        res = await api("POST", `/admin/channels/${general}/announce`, {
            token: admin, body: { body: "wrong channel" } });
        check("announcing into an ordinary channel is refused",
            res.status === 400, `status ${res.status}`);

        // ── search ──────────────────────────────────────────────────────
        console.log("\nSearch");
        res = await api("GET", "/chat/search?q=report", { token: e1 });
        check("search finds a message", res.status === 200 && res.body.total >= 1,
            JSON.stringify(res.body.total));

        await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "reporting the reports repository" } });
        res = await api("GET", "/chat/search?q=report", { token: e1 });
        check("and matches by prefix, so 'report' finds 'reports' and 'reporting'",
            res.body.results.some((r) => r.body.includes("reporting")),
            JSON.stringify(res.body.results.map((r) => r.body)));

        // The reason 'simple' was chosen over 'english': the English stoplist
        // discards these as noise, so a search for them would find nothing.
        await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "ye kaam me kar do na" } });
        res = await api("GET", "/chat/search?q=kar do", { token: e1 });
        check("Hinglish words an English stoplist would discard are searchable",
            res.body.total >= 1, JSON.stringify(res.body.total));

        await api("POST", `/chat/channels/${hrGeneral}/messages`, {
            token: e3, body: { body: "confidential salary review report" } });
        res = await api("GET", "/chat/search?q=salary", { token: e1 });
        check("search NEVER reaches a team you are not in",
            res.body.total === 0, JSON.stringify(res.body.results));
        res = await api("GET", "/chat/search?q=salary", { token: e3 });
        check("but does reach one you are in", res.body.total === 1,
            JSON.stringify(res.body.total));

        res = await api("GET", "/chat/search?q=a", { token: e1 });
        check("a one-character search is refused", res.status === 400, `status ${res.status}`);
        res = await api("GET", "/chat/search?q=" + encodeURIComponent("report & | !()"),
            { token: e1 });
        check("tsquery punctuation cannot crash the search",
            res.status === 200, `status ${res.status}`);

        // ── archive ─────────────────────────────────────────────────────
        console.log("\nArchiving");
        res = await api("POST", `/admin/teams/${hrTeam}/archive`, {
            token: admin, body: { archived: true } });
        check("archiving without a reason is refused", res.status === 400, `status ${res.status}`);

        res = await api("POST", `/admin/teams/${hrTeam}/archive`, {
            token: admin, body: { archived: true, reason: "department merged" } });
        check("archiving with one works", res.status === 200, `status ${res.status}`);

        res = await api("POST", `/chat/channels/${hrGeneral}/messages`, {
            token: e3, body: { body: "still here?" } });
        check("an archived team takes no new messages",
            res.status === 409, `status ${res.status}`);

        res = await api("GET", `/chat/channels/${hrGeneral}/messages`, { token: e3 });
        check("but stays readable", res.status === 200 && res.body.messages.length > 0,
            `status ${res.status}`);
        check("and says so, so the panel can hide the composer",
            res.body.channel.can_post === false);

        res = await api("GET", "/chat/search?q=salary", { token: e3 });
        check("and stays searchable", res.body.total === 1, JSON.stringify(res.body.total));

        check("who archived it and why is on the record",
            psql(DB, `SELECT archived_reason FROM teams WHERE id=${hrTeam}`) === "department merged"
            && psql(DB, `SELECT archived_by FROM teams WHERE id=${hrTeam}`) === "A001");

        res = await api("POST", `/admin/teams/${hrTeam}/archive`, {
            token: sa, body: { archived: false } });
        check("and it can be brought back", res.status === 200, `status ${res.status}`);
        res = await api("POST", `/chat/channels/${hrGeneral}/messages`, {
            token: e3, body: { body: "back again" } });
        check("after which messages are accepted again",
            res.status === 201, `status ${res.status}`);

        // ── the audited read ────────────────────────────────────────────
        console.log("\nReading somebody's conversation");
        res = await api("POST", "/admin/chat/view", {
            token: admin, body: { channel_id: general, purpose: "COMPLAINT",
                                  reference_id: "Complaint #214" } });
        check("an ordinary admin cannot read a conversation",
            res.status === 403, `status ${res.status}`);

        res = await api("POST", "/admin/chat/view", {
            token: e1, body: { channel_id: general, purpose: "COMPLAINT" } });
        check("nor can an employee", res.status === 403, `status ${res.status}`);

        res = await api("POST", "/admin/chat/view", {
            token: sa, body: { channel_id: general } });
        check("a super admin must give a purpose", res.status === 400, `status ${res.status}`);

        res = await api("POST", "/admin/chat/view", {
            token: sa, body: { channel_id: general, purpose: "BECAUSE" } });
        check("and it must be one of the listed ones",
            res.status === 400, `status ${res.status}`);

        res = await api("POST", "/admin/chat/view", {
            token: sa, body: { channel_id: general, purpose: "OTHER" } });
        check("'Other' cannot be used to skip giving a reason",
            res.status === 400, `status ${res.status}`);

        res = await api("POST", "/admin/chat/view", {
            token: sa, body: { channel_id: general, purpose: "COMPLAINT" } });
        check("a reference is required for a complaint",
            res.status === 400, `status ${res.status}`);

        res = await api("POST", "/admin/chat/view", {
            token: sa, body: { channel_id: general, purpose: "COMPLAINT",
                               reference_id: "Complaint #214" } });
        check("with a purpose and a reference, it is allowed",
            res.status === 200, `status ${res.status}`);
        check("and returns the conversation",
            res.body.messages.length > 0, String(res.body.messages.length));
        check("including every version of anything edited",
            res.body.edit_history.some((h) => h.old_body === "Hello sir pls ignore"),
            JSON.stringify(res.body.edit_history.slice(0, 2)));

        check("the read is recorded with who, what and why",
            psql(DB, `SELECT purpose FROM chat_access_log ORDER BY id DESC LIMIT 1`) === "COMPLAINT"
            && psql(DB, `SELECT reference_id FROM chat_access_log ORDER BY id DESC LIMIT 1`) === "Complaint #214"
            && psql(DB, `SELECT viewer_id FROM chat_access_log ORDER BY id DESC LIMIT 1`) === "SA001");
        check("and the viewer's name is stored, not just their id",
            psql(DB, `SELECT viewer_name FROM chat_access_log ORDER BY id DESC LIMIT 1`) === "Owner Sahab");
        check("it also lands in the audit log the weekly report reads",
            Number(psql(DB, `SELECT COUNT(*) FROM activity_logs
                              WHERE activity LIKE 'CHAT VIEWED%'`)) === 1);

        const { auditRowsSql } = require(path.join(root, "server", "utils", "audit_events"));
        check("and is classed as evidence, so the 31-day purge will not take it",
            psql(DB, `SELECT COUNT(*) FROM activity_logs
                       WHERE activity LIKE 'CHAT VIEWED%' AND (${auditRowsSql()})`) === "1");

        res = await api("GET", "/admin/chat/access-log", { token: sa });
        check("the access log reads back", res.status === 200 && res.body.total === 1,
            JSON.stringify(res.body.total));
        check("grouped by purpose, so it can be reported on",
            res.body.by_purpose.some((p) => p.purpose === "COMPLAINT" && p.count === 1),
            JSON.stringify(res.body.by_purpose));
        res = await api("GET", "/admin/chat/access-log", { token: admin });
        check("an ordinary admin cannot read the access log either",
            res.status === 403, `status ${res.status}`);

        // ── a person leaves ─────────────────────────────────────────────
        console.log("\nWhen somebody leaves");
        const beforeDelete = Number(psql(DB,
            `SELECT COUNT(*) FROM messages WHERE sender_id='E001'`));
        check("they had messages to begin with", beforeDelete > 0, String(beforeDelete));

        res = await api("DELETE", "/admin/employees/E001", { token: sa });
        check("deleting the account succeeds", res.status === 200, `status ${res.status}`);

        check("their messages are NOT deleted with them",
            Number(psql(DB, `SELECT COUNT(*) FROM messages WHERE sender_name='Rajesh Kumar'`))
                === beforeDelete);
        check("the link to the account is cleared",
            psql(DB, `SELECT COUNT(*) FROM messages WHERE sender_id='E001'`) === "0");
        check("but the name they sent under survives, so the record is attributable",
            psql(DB, `SELECT DISTINCT sender_name FROM messages
                       WHERE sender_employee_code='E001'`) === "Rajesh Kumar");
        check("and their team membership is gone",
            psql(DB, `SELECT COUNT(*) FROM team_members WHERE employee_id='E001'`) === "0");

        // A wide page on purpose — their messages are behind the hundred sent
        // by the concurrency check, and a default page would simply not reach
        // them, which would look like a pass for the wrong reason.
        res = await api("GET",
            `/chat/channels/${general}/messages?before=${firstSeq + 1}&limit=5`,
            { token: e2 });
        const removed = res.body.messages.find((m) => m.sender_code === "E001");
        check("the panel is told to mark them as a former employee",
            removed && removed.former === true && removed.sender_name === "Rajesh Kumar",
            JSON.stringify(removed));

        // ── nothing is open to the unauthenticated ──────────────────────
        console.log("\nUnauthenticated");
        for (const [method, route] of [
            ["GET", "/chat/me/teams"],
            ["GET", "/chat/updates?since=0"],
            ["GET", "/chat/search?q=report"],
            ["GET", `/chat/channels/${general}/messages`],
            ["POST", `/chat/channels/${general}/messages`],
            ["GET", `/chat/channels/${general}/members`],
            ["POST", `/chat/channels/${general}/read`],
            ["PATCH", `/chat/messages/${editable}`],
            ["GET", "/admin/teams"],
            ["POST", "/admin/teams"],
            ["POST", "/admin/chat/view"],
            ["GET", "/admin/chat/access-log"],
        ]) {
            res = await api(method, route, { body: { body: "x" } });
            check(`${method} ${route.split("?")[0]} needs a token`,
                res.status === 401, `status ${res.status}`);
        }

        // ── presence ────────────────────────────────────────────────────
        console.log("\nPresence");
        psql(DB, `INSERT INTO activity_logs (employee_id, activity, created_at)
                  VALUES ('E002','USER IDLE (75.0s)', NOW() - INTERVAL '14 minutes')`);
        res = await api("GET", `/chat/channels/${general}/members`, { token: e2 });
        check("the member list loads", res.status === 200, `status ${res.status}`);
        const amit = res.body.members.find((m) => m.employee_id === "E002");
        check("and reports a measured idle time, not a self-declared status",
            amit.status === "IDLE" && amit.idle_minutes >= 13 && amit.idle_minutes <= 16,
            JSON.stringify({ status: amit.status, idle: amit.idle_minutes }));

        // ── rate limit ──────────────────────────────────────────────────
        console.log("\nRate limit");
        let limited = 0;
        for (let i = 0; i < 25; i += 1) {
            const r = await api("POST", `/chat/channels/${general}/messages`, {
                token: e2, body: { body: `flood ${i}` } });
            if (r.status === 429) limited += 1;
        }
        check("a flood from one account is throttled", limited > 0, `${limited} refused`);

        server.close();
        await pool.end();

    } finally {
        try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.exit(1);
    }
    console.log("all chat checks passed");
    process.exit(0);
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
