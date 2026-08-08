/**
 * One person to one person.
 *
 * A direct message is a channel with two members and no team, so replies,
 * edits, deletion, attachments, search, unread counts and the gapless `seq`
 * all come for free. What does NOT come for free is who can see it, and that
 * is what most of this file is about.
 *
 * THE RULE THE OWNER SET, in their words: a super admin reads TEAM discussion
 * so that nobody can quietly cause trouble in a company channel. A private
 * conversation between two people is a different thing, and is private.
 *
 * So the super admin's standing access to every channel — which these same
 * tests confirm still holds for team channels — deliberately stops at the
 * door of a direct message. It is not unreachable: the audited route still
 * opens it, demanding a purpose and a reference that are written to a table
 * nothing purges. Private by default, reachable with a reason, never without
 * a record.
 *
 * Run:  node server/tests/test_direct_messages.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_dm_${process.pid}`;
const PORT = 8000 + ((process.pid + 401) % 1000);
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

const login = async (u, device = "d") =>
    (await api("POST", "/auth/login",
        { body: { username: u, password: PASSWORD, device_id: device } })).body.token;

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log(`Direct messages (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(DB, `INSERT INTO employees
                     (employee_id, username, password, role, full_name, designation) VALUES
            ('SA001','superadmin','${hash}','super_admin','Ansh Owner','Founder'),
            ('A001','admin1','${hash}','admin','Priya Nair','Operations Manager'),
            ('E001','rajesh','${hash}','employee','Rajesh Kumar','Backend Developer'),
            ('E002','amit','${hash}','employee','Amit Sharma','QA Engineer'),
            ('E003','sneha','${hash}','employee','Sneha Iyer','Designer'),
            ('E004','gone','${hash}','employee','Suspended Person','Nobody')`);
        psql(DB, `UPDATE employees SET suspended = TRUE WHERE employee_id = 'E004'`);

        Object.assign(process.env, {
            DB_HOST: process.env.PGHOST || "127.0.0.1",
            DB_PORT: process.env.PGPORT || "5432",
            DB_NAME: DB,
            DB_USER: process.env.PGUSER || process.env.USER,
            DB_PASSWORD: process.env.PGPASSWORD || "unused-locally",
            JWT_SECRET: "test-secret-not-used-in-production",
            PORT: String(PORT),
            ENCRYPTION_KEY: "0".repeat(64),
        });

        const { server, pool } = require(path.join(root, "server", "server.js"));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        const sa = await login("superadmin", "sa");
        const admin = await login("admin1", "adm");
        const rajesh = await login("rajesh", "r");
        const amit = await login("amit", "a");
        const sneha = await login("sneha", "s");

        // ── finding somebody ────────────────────────────────────────────
        console.log("Finding who to write to");
        let res = await api("GET", "/chat/people?q=amit", { token: rajesh });
        check("searching by username finds them",
            (res.body.people || []).some((p) => p.employee_id === "E002"),
            JSON.stringify(res.body.people));

        res = await api("GET", "/chat/people?q=Sharma", { token: rajesh });
        check("so does a surname — people look each other up by name",
            (res.body.people || []).some((p) => p.employee_id === "E002"),
            JSON.stringify((res.body.people || []).map((p) => p.employee_id)));

        res = await api("GET", "/chat/people?q=QA", { token: rajesh });
        check("and a job title", (res.body.people || []).some((p) => p.employee_id === "E002"));

        res = await api("GET", "/chat/people?q=", { token: rajesh });
        let ids = (res.body.people || []).map((p) => p.employee_id);
        check("an empty search lists everybody, so the box is usable before typing",
            ids.length >= 4, JSON.stringify(ids));
        check("but never yourself", !ids.includes("E001"), JSON.stringify(ids));
        check("nor a suspended account — that chat would go nowhere",
            !ids.includes("E004"), JSON.stringify(ids));
        check("ADMINS ARE THERE TOO, because anybody may write to anybody",
            ids.includes("A001") && ids.includes("SA001"), JSON.stringify(ids));

        // ── opening one ─────────────────────────────────────────────────
        console.log("\nOpening a conversation");
        res = await api("POST", "/chat/direct", { token: rajesh, body: { employee_id: "E002" } });
        check("it opens", res.status === 200, JSON.stringify(res.body).slice(0, 120));
        const dm = res.body.channel.id;
        check("named after the person, not a channel id",
            res.body.channel.name === "Amit Sharma", res.body.channel.name);
        check("and says who it is with",
            res.body.channel.with.employee_id === "E002", JSON.stringify(res.body.channel.with));

        res = await api("POST", "/chat/direct", { token: rajesh, body: { employee_id: "E002" } });
        check("opening it again returns the SAME conversation, not a second one",
            res.body.channel.id === dm, `${dm} then ${res.body.channel.id}`);

        res = await api("POST", "/chat/direct", { token: amit, body: { employee_id: "E001" } });
        check("and so does the other person opening it from their side",
            res.body.channel.id === dm,
            "two channels for one pair — each would be typing where the other cannot see");

        res = await api("POST", "/chat/direct", { token: rajesh, body: { employee_id: "E001" } });
        check("you cannot open one with yourself", res.status === 400, `status ${res.status}`);
        res = await api("POST", "/chat/direct", { token: rajesh, body: { employee_id: "NOBODY" } });
        check("nor with somebody who does not exist", res.status === 404, `status ${res.status}`);
        res = await api("POST", "/chat/direct", { token: rajesh, body: { employee_id: "E004" } });
        check("nor with a suspended account", res.status === 409, `status ${res.status}`);

        // ── talking ─────────────────────────────────────────────────────
        console.log("\nTalking");
        res = await api("POST", `/chat/channels/${dm}/messages`,
            { token: rajesh, body: { body: "bhai wo build ka issue solve hua?" } });
        check("a message sends", res.status === 201, `status ${res.status}`);
        const first = res.body.message.seq;

        res = await api("POST", `/chat/channels/${dm}/messages`,
            { token: amit, body: { body: "haan ho gaya", reply_to: first } });
        check("and the other side can reply to it", res.status === 201, `status ${res.status}`);
        check("with the quote working, exactly as in a team channel",
            res.body.message.reply && res.body.message.reply.seq === first,
            JSON.stringify(res.body.message.reply));

        // ── the two things that made it unusable ────────────────────────
        res = await api("GET", `/chat/channels/${dm}/messages`, { token: rajesh });
        check("THE COMPOSER IS OPEN — a direct message is always writable",
            res.body.channel.can_post === true,
            "can_post was false, so the panel hid the box and said the team "
            + "was archived, about a conversation with no team");
        check("and it is titled with the person, not the internal pair key",
            res.body.channel.name === "Amit Sharma",
            `showed "${res.body.channel.name}" — EM001:EM002 is a database key`);
        check("with who it is with, for the header",
            res.body.channel.with && res.body.channel.with.employee_id === "E002",
            JSON.stringify(res.body.channel.with));
        check("and it does not claim to be archived",
            res.body.channel.is_archived === false,
            String(res.body.channel.is_archived));

        res = await api("GET", `/chat/channels/${dm}/messages`, { token: amit });
        check("the other side sees the title as the FIRST person",
            res.body.channel.name === "Rajesh Kumar", res.body.channel.name);
        check("both people see the conversation",
            res.status === 200 && res.body.messages.length === 2,
            `status ${res.status}, ${(res.body.messages || []).length} messages`);

        // ── THE PRIVACY, which is the whole point ───────────────────────
        console.log("\nWho else can see it");

        res = await api("GET", `/chat/channels/${dm}/messages`, { token: sneha });
        check("another employee cannot read it", res.status === 404, `status ${res.status}`);
        res = await api("GET", `/chat/channels/${dm}/messages`, { token: admin });
        check("an admin cannot read it", res.status === 404, `status ${res.status}`);
        res = await api("GET", `/chat/channels/${dm}/messages`, { token: sa });
        check("THE SUPER ADMIN CANNOT READ IT EITHER",
            res.status === 404,
            "a private conversation was readable by rank alone — the one thing "
            + "this feature was asked to prevent");

        res = await api("POST", `/chat/channels/${dm}/messages`,
            { token: sa, body: { body: "I am the owner" } });
        check("nor write into it", res.status === 404, `status ${res.status}`);

        res = await api("GET", `/chat/channels/${dm}/members`, { token: sa });
        check("and cannot see who is in it", res.status === 404, `status ${res.status}`);

        res = await api("GET", `/chat/channels/${dm}/members`, { token: rajesh });
        const members = (res.body.members || []).map((m) => m.employee_id);
        check("the two people see exactly each other, with no owner among them",
            members.length === 2 && members.includes("E001") && members.includes("E002"),
            JSON.stringify(members));

        res = await api("GET", "/chat/updates?since=1", { token: sa });
        check("it does not leak through the poll either",
            !JSON.stringify(res.body).includes("build ka issue"),
            "the super admin's poll carried a private message");

        res = await api("GET", "/chat/search?q=build", { token: sa });
        check("nor through search",
            Number(res.body.total || 0) === 0,
            "a private conversation was searchable by somebody outside it");

        res = await api("GET", "/chat/search?q=build", { token: amit });
        check("but the people in it CAN search their own conversation",
            Number(res.body.total || 0) === 1, JSON.stringify(res.body.total));

        // ── the audited route still reaches it ──────────────────────────
        console.log("\nThe recorded way in");
        // Private by default is not the same as unreachable. A complaint has
        // to be answerable — but only with a reason, and never without a
        // record.
        res = await api("POST", "/admin/chat/view",
            { token: sa, body: { channel_id: dm, purpose: "COMPLAINT",
                                 reference_id: "Complaint #7" } });
        check("a super admin with a stated purpose can still read it",
            res.status === 200, `status ${res.status}`);
        check("and sees what was actually said",
            JSON.stringify(res.body.messages || []).includes("build ka issue"),
            "the audited route came back empty");
        check("the read is written to the access log",
            Number(psql(DB, `SELECT COUNT(*) FROM chat_access_log
                              WHERE channel_id = ${dm}`)) === 1);
        check("with the purpose and the reference",
            psql(DB, `SELECT purpose||' / '||reference_id FROM chat_access_log
                       WHERE channel_id = ${dm}`) === "COMPLAINT / Complaint #7");

        res = await api("POST", "/admin/chat/view",
            { token: sa, body: { channel_id: dm, purpose: "COMPLAINT" } });
        check("and it is refused without a reference",
            res.status === 400, `status ${res.status}`);

        res = await api("POST", "/admin/chat/view",
            { token: admin, body: { channel_id: dm, purpose: "COMPLAINT",
                                    reference_id: "C-9" } });
        check("an ordinary admin cannot use that route at all",
            res.status === 403, `status ${res.status}`);

        // ── the conversation list ───────────────────────────────────────
        console.log("\nThe list of conversations");
        await api("POST", "/chat/direct", { token: rajesh, body: { employee_id: "E003" } });
        res = await api("GET", "/chat/directs", { token: rajesh });
        check("both conversations are listed", (res.body.directs || []).length === 2,
            JSON.stringify((res.body.directs || []).length));
        check("the one with the newest message first",
            res.body.directs[0].with.employee_id === "E002",
            JSON.stringify(res.body.directs.map((d) => d.with.employee_id)));
        check("an empty conversation is KEPT, not hidden until somebody types",
            res.body.directs.some((d) => d.with.employee_id === "E003"),
            "opening a chat and watching it vanish is a bug from where the "
            + "person is sitting");
        check("with a preview of the last line",
            res.body.directs[0].preview.includes("ho gaya"),
            res.body.directs[0].preview);
        check("and an unread count",
            typeof res.body.directs[0].unread === "number");

        res = await api("GET", "/chat/directs", { token: sa });
        check("the super admin's own list holds none of other people's",
            (res.body.directs || []).length === 0,
            JSON.stringify(res.body.directs));

        // ── team chat is untouched ──────────────────────────────────────
        console.log("\nTeam chat still works exactly as before");
        // The owner's rule for teams is the opposite one, and changing the
        // access function must not have quietly weakened it.
        const team = (await api("POST", "/admin/teams",
            { token: sa, body: { name: "Development", members: ["E001", "E002"] } }))
            .body.team.id;
        const general = Number(psql(DB,
            `SELECT id FROM channels WHERE team_id=${team} AND is_default`));
        await api("POST", `/chat/channels/${general}/messages`,
            { token: rajesh, body: { body: "team wali baat" } });

        res = await api("GET", `/chat/channels/${general}/messages`, { token: sa });
        check("the super admin still reads every TEAM channel, unasked",
            res.status === 200
            && JSON.stringify(res.body.messages).includes("team wali baat"),
            `status ${res.status}`);

        res = await api("GET", "/chat/me/teams", { token: rajesh });
        const listed = JSON.stringify(res.body.teams || []);
        check("and the team list does not sweep in direct messages",
            !listed.includes("E001:E002"),
            "a DM appeared among the team channels");

        res = await api("GET", `/chat/channels/${general}/messages`, { token: sneha });
        check("somebody outside the team still cannot read its channel",
            res.status === 404, `status ${res.status}`);

        server.close();
        await pool.end();
    } finally {
        try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.stdout.write("", () => process.exit(1));
    } else {
        console.log("all direct message checks passed");
        process.stdout.write("", () => process.exit(0));
    }
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
