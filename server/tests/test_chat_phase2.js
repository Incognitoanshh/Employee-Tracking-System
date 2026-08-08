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
            token: e1, body: { body: "file attached", attachments: [upload.body.attachment.id] } });
        const withFile = res.body.message.seq;
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
