/**
 * Chat, Phase 2 — replies, mentions, pins and files.
 *
 * Each of these adds a new way for one channel's contents to reach somebody
 * who is not in it, and none of them fails loudly when it does:
 *
 *   * A REPLY carries a preview of what it replies to. If reply_to is taken
 *     on trust, a seq from another team's channel prints a slice of that
 *     conversation inside one you are allowed to read.
 *   * A MENTION is a notification. Resolving it against the SENDER's access
 *     instead of the mentioned person's tells somebody a channel exists that
 *     they were deliberately kept out of.
 *   * An ATTACHMENT is fetched by id. Without the same visibility rule, an id
 *     counted upwards in a URL walks every file in the company.
 *   * Claiming an attachment by id must be restricted to the person who
 *     uploaded it, or a guessed id attaches somebody else's file to your
 *     message.
 *
 * Run:  node server/tests/test_chat_phase2.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");
const fs = require("fs");
const os = require("os");

const DB = `ets_chat2_${process.pid}`;
const PORT = 8000 + ((process.pid + 601) % 1000);
const BASE = `http://127.0.0.1:${PORT}/api`;
const PASSWORD = "SuperSecret123";
const UPLOADS = fs.mkdtempSync(path.join(os.tmpdir(), "ets_chat_files_"));

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

async function uploadFile(token, channelId, name, contents) {
    const form = new FormData();
    form.append("file", new Blob([contents]), name);
    form.append("file_name", name);
    form.append("mime_type", "application/octet-stream");
    const response = await fetch(`${BASE}/chat/channels/${channelId}/attachments`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: form,
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return { status: response.status, body: payload };
}

const login = async (u) =>
    (await api("POST", "/auth/login", { body: { username: u, password: PASSWORD } })).body.token;

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`Chat, Phase 2 (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name) VALUES
            ('SA001','superadmin','${hash}','super_admin','Owner'),
            ('A001','admin1','${hash}','admin','Priya Nair'),
            ('E001','rajesh','${hash}','employee','Rajesh Kumar'),
            ('E002','amit','${hash}','employee','Amit Sharma'),
            ('E003','sneha','${hash}','employee','Sneha Iyer'),
            ('E004','vikram','${hash}','employee','Vikram Rao')`);

        Object.assign(process.env, {
            DB_HOST: process.env.PGHOST || "127.0.0.1",
            DB_PORT: process.env.PGPORT || "5432",
            DB_NAME: DB,
            DB_USER: process.env.PGUSER || process.env.USER,
            DB_PASSWORD: process.env.PGPASSWORD || "unused-locally",
            JWT_SECRET: "test-secret-not-used-in-production",
            PORT: String(PORT),
            ENCRYPTION_KEY: "0".repeat(64),
            CHAT_UPLOAD_DIR: UPLOADS,
        });

        const { server, pool } = require(path.join(root, "server", "server.js"));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        const admin = await login("admin1");
        const e1 = await login("rajesh");
        const e2 = await login("amit");
        const e3 = await login("sneha");
        const e4 = await login("vikram");

        // Development: rajesh, amit, sneha. Backend: rajesh + amit only.
        // HR: vikram — used as the outsider throughout.
        let res = await api("POST", "/admin/teams", {
            token: admin,
            body: { name: "Development", members: ["E001", "E002", "E003"] } });
        const devTeam = res.body.team.id;
        res = await api("POST", `/admin/teams/${devTeam}/channels`, {
            token: admin, body: { name: "Backend", members: ["E001", "E002"] } });
        const backend = res.body.channel.id;
        res = await api("POST", "/admin/teams", {
            token: admin, body: { name: "HR", members: ["E004"] } });
        const hrTeam = res.body.team.id;

        const general = Number(psql(DB,
            `SELECT id FROM channels WHERE team_id=${devTeam} AND is_default`));
        const hrGeneral = Number(psql(DB,
            `SELECT id FROM channels WHERE team_id=${hrTeam} AND is_default`));

        // ── replies ─────────────────────────────────────────────────────
        console.log("Replies");
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "kal ka report bhej dena" } });
        const parent = res.body.message.seq;

        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e2, body: { body: "bhej diya", reply_to: parent } });
        check("a reply is accepted", res.status === 201, `status ${res.status}`);
        check("and carries a preview of what it answers",
            res.body.message.reply
            && res.body.message.reply.sender_name === "Rajesh Kumar"
            && res.body.message.reply.excerpt.startsWith("kal ka report"),
            JSON.stringify(res.body.message.reply));

        res = await api("POST", `/chat/channels/${hrGeneral}/messages`, {
            token: e4, body: { body: "confidential salary discussion" } });
        const hrMessage = res.body.message.seq;

        // The one that matters: replying across channels would print a slice
        // of the other conversation as the preview.
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "what is this", reply_to: hrMessage } });
        check("replying to another team's message is REFUSED",
            res.status === 400, `status ${res.status}`);
        check("and the reply says why, rather than failing silently",
            /this channel/i.test(res.body.message || ""), res.body.message);
        check("so nothing of that conversation leaks",
            !JSON.stringify(res.body).includes("salary"), JSON.stringify(res.body));

        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "x", reply_to: 999999 } });
        check("replying to a message that does not exist is refused",
            res.status === 400, `status ${res.status}`);

        // ── mentions ────────────────────────────────────────────────────
        console.log("\nMentions");
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "@amit ye dekh lena please" } });
        check("an @username in the text is recognised",
            res.body.mentioned === 1, JSON.stringify(res.body.mentioned));
        const mentionSeq = res.body.message.seq;
        check("and the message says who it names",
            res.body.message.mentions.some((m) => m.employee_id === "E002"),
            JSON.stringify(res.body.message.mentions));

        check("a notification is raised, not just an unread count",
            psql(DB, `SELECT COUNT(*) FROM notifications
                       WHERE employee_id='E002' AND type='MENTION'`) === "1");

        res = await api("GET", `/chat/channels/${general}/messages`, { token: e2 });
        let mine = res.body.messages.find((m) => m.seq === mentionSeq);
        check("the mentioned person's copy is flagged for them",
            mine.mentions_me === true, JSON.stringify(mine.mentions_me));
        res = await api("GET", `/chat/channels/${general}/messages`, { token: e3 });
        mine = res.body.messages.find((m) => m.seq === mentionSeq);
        check("and somebody else's copy is not",
            mine.mentions_me === false, JSON.stringify(mine.mentions_me));

        // The important one: naming somebody who cannot see the channel.
        res = await api("POST", `/chat/channels/${backend}/messages`, {
            token: e1, body: { body: "@sneha ye backend ka issue hai" } });
        check("naming somebody who cannot see the channel mentions nobody",
            res.body.mentioned === 0, JSON.stringify(res.body.mentioned));
        check("and raises no notification that would reveal the channel",
            psql(DB, `SELECT COUNT(*) FROM notifications
                       WHERE employee_id='E003' AND type='MENTION'`) === "0");

        // Explicit ids from the autocomplete, which is how names with spaces
        // are handled — "@Rajesh Kumar" cannot be recovered from the text.
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e2, body: { body: "@Rajesh Kumar dekh lo", mentions: ["E001"] } });
        check("an id from the autocomplete works where the text cannot",
            res.body.mentioned === 1, JSON.stringify(res.body.mentioned));

        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e2, body: { body: "hello", mentions: ["E004"] } });
        check("an id for somebody outside the channel is dropped too",
            res.body.mentioned === 0, JSON.stringify(res.body.mentioned));

        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e2, body: { body: "@amit talking about myself" } });
        check("mentioning yourself notifies nobody",
            res.body.mentioned === 0, JSON.stringify(res.body.mentioned));

        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "mail me at someone@example.com" } });
        check("an email address is not a mention",
            res.body.mentioned === 0, JSON.stringify(res.body.mentioned));

        // ── pins ────────────────────────────────────────────────────────
        console.log("\nPins");
        res = await api("POST", `/chat/messages/${parent}/pin`, {
            token: e2, body: { pinned: true } });
        check("any member of the channel can pin", res.status === 200, `status ${res.status}`);

        res = await api("GET", `/chat/channels/${general}/pinned`, { token: e3 });
        check("the pinned message is listed for everyone",
            res.body.messages.length === 1 && res.body.messages[0].seq === parent,
            JSON.stringify(res.body.messages.map((m) => m.seq)));
        check("and says who pinned it, so it is not an unexplained fixture",
            res.body.messages[0].pinned_by_name === "Amit Sharma",
            res.body.messages[0].pinned_by_name);

        res = await api("GET", `/chat/channels/${general}/messages`, { token: e1 });
        check("the message itself is marked pinned in the conversation",
            res.body.messages.find((m) => m.seq === parent).pinned === true);

        res = await api("POST", `/chat/messages/${parent}/pin`, {
            token: e4, body: { pinned: true } });
        check("somebody outside the channel cannot pin — and gets 404, not 403",
            res.status === 404, `status ${res.status}`);

        res = await api("POST", `/chat/messages/${hrMessage}/pin`, {
            token: e1, body: { pinned: true } });
        check("nor pin a message in a channel they cannot see",
            res.status === 404, `status ${res.status}`);

        // A shelf of fifty is not a shelf.
        // The filler messages exist only to be pinned, so they are inserted
        // directly. Sending twenty-one through the API would trip the rate
        // limiter and the test would be measuring that instead — which it did
        // on the first run, and reported as a pin failure.
        const { MAX_PINNED } = require(path.join(root, "server", "controllers", "chat.controller"));
        psql(DB, `INSERT INTO messages (channel_id, sender_id, sender_name, body)
                  SELECT ${general}, 'E001', 'Rajesh Kumar', 'pin filler ' || i
                    FROM generate_series(1, ${MAX_PINNED + 1}) i`);
        const fillers = psql(DB,
            `SELECT string_agg(seq::text, ',') FROM (
               SELECT seq FROM messages WHERE channel_id=${general}
                 AND body LIKE 'pin filler%' ORDER BY seq) s`).split(",").map(Number);

        for (let i = 0; i < MAX_PINNED; i += 1) {
            await api("POST", `/chat/messages/${fillers[i]}/pin`,
                { token: e1, body: { pinned: true } });
        }
        res = await api("POST", `/chat/messages/${fillers[MAX_PINNED]}/pin`, {
            token: e1, body: { pinned: true } });
        check(`pinning stops at ${MAX_PINNED}`, res.status === 409, `status ${res.status}`);
        check("and says what to do about it",
            /unpin one/i.test(res.body.message || ""), res.body.message);

        res = await api("POST", `/chat/messages/${parent}/pin`, {
            token: e3, body: { pinned: false } });
        check("anyone in the channel can unpin", res.status === 200, `status ${res.status}`);
        check("and the record of who pinned it is cleared",
            psql(DB, `SELECT COALESCE(pinned_by,'-') FROM messages WHERE seq=${parent}`) === "-");

        // ── attachments ─────────────────────────────────────────────────
        console.log("\nFiles");
        let up = await uploadFile(e1, general, "report.pdf.enc", "encrypted-bytes-here");
        check("a file uploads", up.status === 201, `status ${up.status}`);
        const fileId = up.body.attachment.id;
        check("it is not attached to anything yet",
            psql(DB, `SELECT message_seq IS NULL FROM attachments WHERE id=${fileId}`) === "t");

        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "", attachment_ids: [fileId] } });
        check("a message carrying a file needs no words",
            res.status === 201, `status ${res.status}`);
        check("and the file is now part of it",
            res.body.message.attachments.length === 1
            && res.body.message.attachments[0].file_name === "report.pdf.enc",
            JSON.stringify(res.body.message.attachments));

        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "" } });
        check("but a message with neither words nor a file is still refused",
            res.status === 400, `status ${res.status}`);

        // Downloading.
        let download = await fetch(`${BASE}/chat/attachments/${fileId}`,
            { headers: { Authorization: `Bearer ${e2}` } });
        check("another member can download it", download.status === 200,
            `status ${download.status}`);
        check("and gets the bytes back unchanged",
            (await download.text()) === "encrypted-bytes-here");

        download = await fetch(`${BASE}/chat/attachments/${fileId}`,
            { headers: { Authorization: `Bearer ${e4}` } });
        check("somebody outside the channel CANNOT — this is the id-walking case",
            download.status === 404, `status ${download.status}`);

        download = await fetch(`${BASE}/chat/attachments/${fileId}`);
        check("and neither can anybody unauthenticated",
            download.status === 401, `status ${download.status}`);

        // Claiming a file you did not upload.
        up = await uploadFile(e2, general, "amit-private.enc", "amit's bytes");
        const amitFile = up.body.attachment.id;
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "mine now", attachment_ids: [amitFile] } });
        check("you cannot attach somebody else's uploaded file to your message",
            res.body.message.attachments.length === 0,
            JSON.stringify(res.body.message.attachments));
        check("and it stays unclaimed rather than being quietly stolen",
            psql(DB, `SELECT message_seq IS NULL FROM attachments WHERE id=${amitFile}`) === "t");

        up = await uploadFile(e4, general, "outsider.enc", "nope");
        check("an outsider cannot upload into the channel at all",
            up.status === 404, `status ${up.status}`);

        const announce = (await api("POST", `/admin/teams/${devTeam}/channels`, {
            token: admin, body: { name: "Notices", type: "ANNOUNCEMENT" } })).body.channel.id;
        up = await uploadFile(e1, announce, "sneaky.enc", "nope");
        check("nor can anyone upload into an announcement channel",
            up.status === 403, `status ${up.status}`);

        const rejected = fs.readdirSync(UPLOADS).filter((f) => f.includes("E004"));
        check("a rejected upload does not leave a file behind on disk",
            rejected.length === 0, rejected.join(", "));

        // ── orphans ─────────────────────────────────────────────────────
        console.log("\nUnclaimed uploads");
        up = await uploadFile(e1, general, "abandoned.enc", "changed my mind");
        check("an upload nobody sends stays unclaimed",
            psql(DB, `SELECT COUNT(*) FROM attachments WHERE message_seq IS NULL`) !== "0");
        check("and is findable for sweeping",
            Number(psql(DB, `SELECT COUNT(*) FROM attachments
                              WHERE message_seq IS NULL`)) >= 2);

        // ── still true from Phase 1 ─────────────────────────────────────
        console.log("\nPhase 1 rules still hold");
        res = await api("GET", `/chat/channels/${backend}/messages`, { token: e3 });
        check("a channel you are not in is still invisible",
            res.status === 404, `status ${res.status}`);
        res = await api("GET", "/chat/search?q=salary", { token: e1 });
        check("search still cannot reach another team",
            res.body.total === 0, JSON.stringify(res.body.total));


        // ── deletion ────────────────────────────────────────────────────
        //
        // Deleting is a flag, not a DELETE. Everything below exists to check
        // that the two halves of that promise both hold: the channel really
        // stops showing it, and the record really keeps it.
        console.log("\nDeleting a message");

        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "galti se bhej diya, ignore karo" } });
        const doomed = res.body.message.seq;

        res = await api("DELETE", `/chat/messages/${doomed}`, { token: e2 });
        check("somebody else cannot delete your message",
            res.status === 403, `status ${res.status}`);

        res = await api("DELETE", `/chat/messages/${doomed}`, { token: e1 });
        check("but you can delete your own", res.status === 200, `status ${res.status}`);

        res = await api("GET", `/chat/channels/${general}/messages`, { token: e2 });
        let gone = res.body.messages.find((m) => m.seq === doomed);
        check("it is still IN the conversation, not silently missing",
            Boolean(gone), "the message vanished, leaving replies pointing at nothing");
        check("marked deleted, so the panel can say so",
            gone && gone.deleted === true, JSON.stringify(gone && gone.deleted));
        check("and the words are gone from what anybody is sent",
            !JSON.stringify(res.body).includes("galti se"),
            "the deleted text was still in the response");

        // Polling is a separate read path and had its own WHERE clause.
        res = await api("GET", `/chat/updates?since=${doomed - 1}`, { token: e2 });
        const polled = (res.body.messages || []).find((m) => m.seq === doomed);
        check("polling reports the deletion too, so an open panel updates",
            Boolean(polled) && polled.deleted === true, JSON.stringify(polled));
        check("and leaks nothing either",
            !JSON.stringify(res.body).includes("galti se"), "poll returned the text");

        // The one a cursor cannot carry: an OLD message withdrawn after
        // everybody has already polled past it. Without a separate channel for
        // deletions, every open panel keeps showing the text for as long as it
        // stays open.
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "purana message jo baad me hataunga" } });
        const oldOne = res.body.message.seq;
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e2, body: { body: "aur uske baad ka" } });
        const newerCursor = res.body.message.seq;
        await api("DELETE", `/chat/messages/${oldOne}`, { token: e1 });

        res = await api("GET", `/chat/updates?since=${newerCursor}`, { token: e3 });
        check("a panel already past a message still learns it was withdrawn",
            (res.body.deletions || []).includes(oldOne),
            JSON.stringify(res.body.deletions));

        res = await api("GET", `/chat/updates?since=${newerCursor}`, { token: e4 });
        check("but not from a team it is not in",
            !(res.body.deletions || []).includes(oldOne),
            JSON.stringify(res.body.deletions));

        res = await api("GET", "/chat/search?q=galti", { token: e1 });
        check("a deleted message cannot be found by searching for its words",
            res.body.total === 0, JSON.stringify(res.body.total));

        // The record. This is the half a hard DELETE would have thrown away.
        const kept = psql(DB, `SELECT body FROM messages WHERE seq = ${doomed}`);
        check("the text is STILL IN THE DATABASE — deletion is not erasure",
            kept.includes("galti se"), `stored body: ${JSON.stringify(kept)}`);
        const stamped = psql(DB,
            `SELECT deleted_by FROM messages WHERE seq = ${doomed}`);
        check("and who withdrew it is recorded", stamped === "E001", stamped);

        res = await api("POST", "/admin/chat/view", {
            token: await login("superadmin"),
            body: { channel_id: general, purpose: "COMPLAINT", reference_id: "C-1" } });
        const audited = (res.body.messages || []).find((m) => m.seq === doomed);
        check("the audited view still shows what was actually said",
            Boolean(audited) && String(audited.body).includes("galti se"),
            JSON.stringify(audited && audited.body));
        check("and shows that it was withdrawn, so the reader is not misled",
            Boolean(audited) && audited.deleted === true,
            JSON.stringify(audited && audited.deleted));

        // A deleted message must not be editable back into existence.
        res = await api("PATCH", `/chat/messages/${doomed}`, {
            token: e1, body: { body: "actually never mind" } });
        check("a deleted message cannot be edited back to life",
            res.status === 404, `status ${res.status}`);

        res = await api("DELETE", `/chat/messages/${doomed}`, { token: e1 });
        check("deleting twice is not an error — a slow link means double clicks",
            res.status === 200, `status ${res.status}`);

        // Files and quotes: the two ways a deleted message keeps speaking.
        const upload = await uploadFile(e1, general, "secret.txt", "confidential");
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "file attached",
                               attachment_ids: [upload.body.attachment.id] } });
        const withFile = res.body.message.seq;
        // Proving the attachment is actually ON the message before deleting
        // it. The field is attachment_ids; this said `attachments`, which the
        // server ignores — so the check below passed because there was never
        // an attachment to strip, not because deletion stripped one.
        check("the file really is attached before the message is deleted",
            (res.body.message.attachments || []).length === 1,
            JSON.stringify(res.body.message.attachments));
        await api("DELETE", `/chat/messages/${withFile}`, { token: e1 });
        res = await api("GET", `/chat/channels/${general}/messages`, { token: e2 });
        const stripped = res.body.messages.find((m) => m.seq === withFile);
        check("deleting a message takes its attachment down with it",
            stripped && stripped.attachments.length === 0,
            JSON.stringify(stripped && stripped.attachments));

        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "meeting 4 baje, secret code is ALPHA" } });
        const quoted = res.body.message.seq;
        await api("POST", `/chat/channels/${general}/messages`, {
            token: e2, body: { body: "theek hai", reply_to: quoted } });
        await api("DELETE", `/chat/messages/${quoted}`, { token: e1 });
        res = await api("GET", `/chat/channels/${general}/messages`, { token: e3 });
        check("a reply stops quoting a message that has been withdrawn",
            !JSON.stringify(res.body).includes("ALPHA"),
            "the deleted text survived inside somebody else's reply");

        // Pinned and then deleted: the shelf must not keep a tombstone.
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "pin karke delete karta hoon" } });
        const pinnedThenGone = res.body.message.seq;
        await api("POST", `/chat/messages/${pinnedThenGone}/pin`, {
            token: e1, body: { pinned: true } });
        await api("DELETE", `/chat/messages/${pinnedThenGone}`, { token: e1 });
        res = await api("GET", `/chat/channels/${general}/pinned`, { token: e1 });
        check("deleting a pinned message unpins it, leaving no tombstone on the shelf",
            !res.body.messages.some((m) => m.seq === pinnedThenGone),
            JSON.stringify(res.body.messages.map((m) => m.seq)));

        res = await api("DELETE", `/chat/messages/${hrMessage}`, { token: e1 });
        check("a message in a channel you cannot see reads as not found, not forbidden",
            res.status === 404, `status ${res.status}`);

        for (const [method, route] of [
            ["DELETE", `/chat/messages/${parent}`],
            ["POST", `/chat/messages/${parent}/pin`],
            ["GET", `/chat/channels/${general}/pinned`],
            ["POST", `/chat/channels/${general}/attachments`],
            ["GET", `/chat/attachments/${fileId}`],
        ]) {
            const r = await api(method, route, { body: { pinned: true } });
            check(`${method} ${route.replace(/\d+/g, ":id")} needs a token`,
                r.status === 401, `status ${r.status}`);
        }

        // ── reactions ───────────────────────────────────────────────────
        console.log("\nReactions");
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "deploy done" } });
        const reactable = res.body.message.seq;

        res = await api("GET", "/chat/reactions", { token: e2 });
        check("the choices come from the server",
            res.status === 200 && Array.isArray(res.body.reactions)
            && res.body.reactions.length > 0,
            JSON.stringify(res.body).slice(0, 90));
        const thumb = res.body.reactions[0];

        res = await api("POST", `/chat/messages/${reactable}/reactions`,
            { token: e2, body: { emoji: thumb } });
        check("a reaction is accepted", res.status === 200, `status ${res.status}`);
        check("and it comes back as mine", res.body.mine === true,
            JSON.stringify(res.body).slice(0, 90));
        check("counted once", (res.body.reactions[0] || {}).count === 1,
            JSON.stringify(res.body.reactions));

        // THE SAME CALL TAKES IT AWAY. Pressing the same button twice is what
        // a person means by "undo"; two endpoints would need the client to
        // know which state it is in, and it already draws that state.
        res = await api("POST", `/chat/messages/${reactable}/reactions`,
            { token: e2, body: { emoji: thumb } });
        check("pressing it again removes it", res.body.mine === false,
            JSON.stringify(res.body).slice(0, 90));
        check("and the count goes with it",
            (res.body.reactions.length === 0), JSON.stringify(res.body.reactions));

        // Two people, one emoji.
        await api("POST", `/chat/messages/${reactable}/reactions`,
            { token: e1, body: { emoji: thumb } });
        res = await api("POST", `/chat/messages/${reactable}/reactions`,
            { token: e2, body: { emoji: thumb } });
        check("two people counted together",
            (res.body.reactions[0] || {}).count === 2,
            JSON.stringify(res.body.reactions));

        res = await api("GET", `/chat/channels/${general}/messages`, { token: e2 });
        const carried = (res.body.messages || []).find((m) => m.seq === reactable);
        check("reactions travel with the message, not a request each",
            carried && carried.reactions && carried.reactions[0].count === 2,
            JSON.stringify(carried && carried.reactions));
        check("and each reader is told which are theirs",
            carried.reactions[0].mine === true, JSON.stringify(carried.reactions));

        res = await api("POST", `/chat/messages/${reactable}/reactions`,
            { token: e2, body: { emoji: "<script>" } });
        check("anything outside the list is refused", res.status === 400,
            `status ${res.status}`);

        // Somebody who cannot see the channel cannot react to what is in it,
        // and is told the same thing as if the message did not exist.
        //
        // e4, NOT e3. Sneha is in Development and can see this channel — the
        // first draft used her and the check passed for the wrong reason,
        // reporting 200 as a failure of the code rather than of the test.
        // Vikram is in HR only.
        res = await api("POST", `/chat/messages/${reactable}/reactions`,
            { token: e4, body: { emoji: thumb } });
        check("an outsider gets the same answer as for a missing message",
            res.status === 404, `status ${res.status}`);

        // ── typing ──────────────────────────────────────────────────────
        console.log("\nTyping");
        res = await api("POST", `/chat/channels/${general}/typing`, { token: e1 });
        check("a ping is accepted and says nothing back",
            res.status === 204, `status ${res.status}`);

        res = await api("GET", `/chat/channels/${general}/typing`, { token: e2 });
        check("somebody else sees it",
            (res.body.typing || []).some((t) => t.employee_id === "E001"),
            JSON.stringify(res.body.typing));
        check("with a name, not just an id",
            (res.body.typing || [])[0].name === "Rajesh Kumar",
            JSON.stringify(res.body.typing));

        res = await api("GET", `/chat/channels/${general}/typing`, { token: e1 });
        check("but the typist is not told about themselves",
            (res.body.typing || []).length === 0,
            JSON.stringify(res.body.typing));

        res = await api("DELETE", `/chat/channels/${general}/typing`, { token: e1 });
        check("stopping is accepted", res.status === 204, `status ${res.status}`);
        res = await api("GET", `/chat/channels/${general}/typing`, { token: e2 });
        check("and the indicator goes at once",
            (res.body.typing || []).length === 0,
            JSON.stringify(res.body.typing));

        // A ROW THAT HAS LAPSED IS NOT SHOWN, AND IS TIDIED AWAY.
        //
        // This is the failure everybody has seen in some chat app: somebody
        // closes their laptop mid-sentence and "typing…" stays up for ever,
        // because the only thing that clears it is a message that never
        // comes. Every row carries its own expiry for that reason.
        await api("POST", `/chat/channels/${general}/typing`, { token: e1 });
        psql(DB, `UPDATE typing_state
                     SET expires_at = (NOW() AT TIME ZONE 'UTC') - INTERVAL '1 minute'
                   WHERE employee_id = 'E001'`);
        res = await api("GET", `/chat/channels/${general}/typing`, { token: e2 });
        check("a lapsed ping is not shown", (res.body.typing || []).length === 0,
            JSON.stringify(res.body.typing));
        check("and the row is gone, without a sweeper to run",
            Number(psql(DB, `SELECT COUNT(*) FROM typing_state`)) === 0,
            psql(DB, `SELECT COUNT(*) FROM typing_state`));

        // Somebody outside the channel can neither say nor see who is typing.
        res = await api("POST", `/chat/channels/${general}/typing`, { token: e4 });
        check("an outsider cannot announce themselves", res.status === 404,
            `status ${res.status}`);
        res = await api("GET", `/chat/channels/${general}/typing`, { token: e4 });
        check("nor read who is", res.status === 404, `status ${res.status}`);

        // ── threads ─────────────────────────────────────────────────────
        console.log("\nThreads");
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "release plan?" } });
        const threadRoot = res.body.message.seq;
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e2, body: { body: "friday", reply_to: threadRoot } });
        const firstReply = res.body.message.seq;
        await api("POST", `/chat/channels/${general}/messages`, {
            token: e3, body: { body: "works for me", reply_to: threadRoot } });

        res = await api("GET", `/chat/messages/${threadRoot}/thread`, { token: e1 });
        check("the thread has its root", res.body.root
            && res.body.root.seq === threadRoot, JSON.stringify(res.body.root || {}).slice(0, 80));
        check("and both replies, oldest first",
            (res.body.replies || []).map((m) => m.body).join("|") === "friday|works for me",
            JSON.stringify((res.body.replies || []).map((m) => m.body)));

        // OPENING A REPLY OPENS THE SAME THREAD. Somebody clicking the third
        // message means "show me this discussion"; a thread of one is a dead
        // end they have to back out of.
        res = await api("GET", `/chat/messages/${firstReply}/thread`, { token: e2 });
        check("opening a reply lands on the same thread",
            res.body.root && res.body.root.seq === threadRoot,
            JSON.stringify(res.body.root || {}).slice(0, 80));

        // The count travels with the message, so the conversation can offer
        // "2 replies" without a request per line.
        res = await api("GET", `/chat/channels/${general}/messages`, { token: e1 });
        const withCount = (res.body.messages || []).find((m) => m.seq === threadRoot);
        check("the root carries its reply count", withCount.reply_count === 2,
            String(withCount.reply_count));
        const leaf = (res.body.messages || []).find((m) => m.seq === firstReply);
        check("and a reply carries none of its own — threads are one deep",
            leaf.reply_count === 0, String(leaf.reply_count));

        // A REPLY TO A REPLY JOINS THE SAME THREAD rather than nesting.
        await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "agreed", reply_to: firstReply } });
        res = await api("GET", `/chat/messages/${threadRoot}/thread`, { token: e1 });
        check("a reply to a reply is not a second thread",
            (res.body.replies || []).length === 2,
            `${(res.body.replies || []).length} replies on the root — `
            + `the nested one hangs off ${firstReply}, which is where it was sent`);

        res = await api("GET", `/chat/messages/${threadRoot}/thread`, { token: e4 });
        check("somebody outside the channel gets nothing",
            res.status === 404, `status ${res.status}`);

        // ── mentions: the handle, and the notification ──────────────────
        console.log("\nMentions by name");
        res = await api("GET", `/chat/channels/${general}/members`, { token: e1 });
        const listed = (res.body.members || []).find((m) => m.employee_id === "E002");
        // THE HANDLE WAS MISSING FROM THIS PAYLOAD. The panel writes
        // "@" + username and falls back to the employee id when it has none,
        // so every mention picked from the list came out as "@E002" — and
        // the server, matching handles against usernames, resolved it to
        // nobody. No highlight, no notification, and no sign of failure.
        check("the member list carries the handle, not just a name",
            listed && listed.username === "amit",
            JSON.stringify(listed || {}).slice(0, 120));

        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "@amit please look" } });
        const byHandle = res.body.message.seq;
        check("a mention by username notifies",
            Number(psql(DB, `SELECT COUNT(*) FROM notifications
                              WHERE employee_id='E002' AND type='MENTION'
                                AND message_seq=${byHandle}`)) === 1,
            "notifications for E002");

        // AND BY EMPLOYEE ID, because people type that from memory — and
        // because the panel itself did for as long as the handle was
        // missing above.
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "@E002 and again" } });
        const byId = res.body.message.seq;
        check("a mention by employee id notifies too",
            Number(psql(DB, `SELECT COUNT(*) FROM notifications
                              WHERE employee_id='E002' AND type='MENTION'
                                AND message_seq=${byId}`)) === 1,
            "notifications for E002 by id");

        // Still nobody outside the channel, however they are named.
        res = await api("POST", `/chat/channels/${general}/messages`, {
            token: e1, body: { body: "@vikram you too" } });
        check("somebody who cannot see the channel is not notified",
            Number(psql(DB, `SELECT COUNT(*) FROM notifications
                              WHERE employee_id='E004' AND type='MENTION'`)) === 0,
            "a mention must not reach into a channel somebody cannot see");

        server.close();
        await pool.end();

    } finally {
        try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
        try { fs.rmSync(UPLOADS, { recursive: true, force: true }); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.exit(1);
    }
    console.log("all Phase 2 chat checks passed");
    process.exit(0);
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    try { fs.rmSync(UPLOADS, { recursive: true, force: true }); } catch (_) {}
    process.exit(1);
});
