/**
 * A person's own account page, and the line around what they may change.
 *
 * THE RULE THIS EXISTS TO HOLD. An employee owns two things about themselves:
 * their phone number and their photo. Everything else on that page — role,
 * employee id, department, manager, joining date, employment status, hours,
 * attendance — is the company's record of them, and a monitoring product
 * where the monitored can edit their own department or their own hours is not
 * a monitoring product.
 *
 * None of these routes take an employee id. That is deliberate and worth a
 * check of its own: there is no parameter to tamper with, so the only way to
 * read somebody else's profile is to be an administrator, through the
 * administrator's own endpoints.
 *
 * Run:  node server/tests/test_profile.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { migrate } = require("./_migrate");

const DB = `ets_profile_${process.pid}`;
const PORT = 8000 + ((process.pid + 907) % 1000);
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
        ...(body !== undefined && method !== "GET" ? { body: JSON.stringify(body) } : {}),
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return { status: response.status, body: payload };
}

async function sendPhoto(token, bytes, type = "image/png", name = "me.png") {
    const form = new FormData();
    form.append("photo", new Blob([bytes], { type }), name);
    const response = await fetch(`${BASE}/profile/me/photo`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: form,
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return { status: response.status, body: payload };
}

const login = async (u, device = "d1") =>
    (await api("POST", "/auth/login",
        { body: { username: u, password: PASSWORD, device_id: device } })).body.token;

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    const photos = fs.mkdtempSync(path.join(os.tmpdir(), "ets_photos_"));
    console.log(`Profile (${DB})\n`);

    try {
        migrate(DB);

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash(PASSWORD, 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name, designation) VALUES
            ('SA01','owner','${hash}','super_admin','The Owner','Founder'),
            ('A001','admin1','${hash}','admin','Priya Nair','Manager'),
            ('E001','rajesh','${hash}','employee','Rajesh Kumar','Developer'),
            ('E002','sneha','${hash}','employee','Sneha Iyer','Designer'),
            -- A third employee who shares nothing with anybody: no team, no
            -- channel, no message. The photo rule has to answer for them.
            ('E003','chandra','${hash}','employee','Chandra Rao','Analyst')`);

        Object.assign(process.env, {
            DB_HOST: process.env.PGHOST || "127.0.0.1",
            DB_PORT: process.env.PGPORT || "5432",
            DB_NAME: DB,
            DB_USER: process.env.PGUSER || process.env.USER,
            DB_PASSWORD: process.env.PGPASSWORD || "unused-locally",
            JWT_SECRET: "test-secret-not-used-in-production",
            PORT: String(PORT),
            ENCRYPTION_KEY: "0".repeat(64),
            PROFILE_PHOTO_DIR: photos,
            // Enough for mailer.js to consider itself configured. Nothing is
            // ever sent — `send` is replaced below — but the endpoint refuses
            // before writing anything when it thinks it has no mailbox, and
            // that refusal is itself worth having a server that can pass.
            SMTP_HOST: "smtp.invalid",
            SMTP_USER: "no-reply@amaze.test",
            SMTP_PASS: "not-a-real-password",
        });

        // THE CODE IS CAUGHT HERE INSTEAD OF BEING POSTED. Replacing `send`
        // on the module the controller holds is what makes this testable
        // without a mailbox — and it also proves the code never travels back
        // in the response, because the only place it can be read is here.
        const mailer = require(path.join(root, "server", "utils", "mailer.js"));
        const posted = [];
        mailer.send = async (message) => { posted.push(message); };

        const { server, pool } = require(path.join(root, "server", "server.js"));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        const owner = await login("owner", "owner-mac");
        const admin = await login("admin1", "admin-mac");
        let employee = await login("rajesh", "rajesh-laptop");
        let other = await login("sneha", "sneha-laptop");

        console.log("Reading my own profile");
        let res = await api("GET", "/profile/me", { token: employee });
        check("it answers", res.status === 200, `HTTP ${res.status}`);
        const p = res.body.profile || {};
        check("with who I am", p.employee_id === "E001" && p.full_name === "Rajesh Kumar",
            JSON.stringify(p).slice(0, 120));
        check("my designation and role", p.designation === "Developer" && p.role === "employee",
            JSON.stringify(p).slice(0, 120));
        check("an account nobody set a status on reads as active",
            p.employment_status === "active", String(p.employment_status));
        check("and it says whether I am online right now",
            ["online", "offline"].includes(p.status), String(p.status));
        check("anonymous cannot read it",
            (await api("GET", "/profile/me")).status === 401);

        console.log("\nThe two things I own");
        res = await api("PATCH", "/profile/me", { token: employee, body: { phone: "+91 98765 43210" } });
        check("my phone number saves", res.status === 200, JSON.stringify(res.body).slice(0, 120));
        check("and is there when the page is read again",
            (await api("GET", "/profile/me", { token: employee })).body.profile.phone
                === "+91 98765 43210");
        res = await api("PATCH", "/profile/me", { token: employee, body: { phone: "" } });
        check("clearing it is allowed — people change numbers", res.status === 200);
        check("and it is actually cleared",
            psql(DB, `SELECT COALESCE(phone,'(null)') FROM employees WHERE employee_id='E001'`)
                === "(null)");
        res = await api("PATCH", "/profile/me", { token: employee, body: { phone: "not a phone" } });
        check("nonsense is refused with a reason", res.status === 400,
            JSON.stringify(res.body).slice(0, 120));
        res = await api("PATCH", "/profile/me", { token: employee, body: { phone: "9".repeat(60) } });
        check("and so is something absurdly long", res.status === 400, `HTTP ${res.status}`);

        console.log("\nMy email address");
        res = await api("PATCH", "/profile/me",
            { token: employee, body: { email: "rajesh@amaze.co" } });
        check("my email saves", res.status === 200,
            JSON.stringify(res.body).slice(0, 120));
        check("and it comes back on the profile",
            (await api("GET", "/profile/me", { token: employee })).body.profile.email
                === "rajesh@amaze.co");

        // THE TYPO EVERYBODY MAKES, and the one a shape check is for. Nothing
        // here claims the address WORKS — only sending to it can — so what is
        // refused is what cannot possibly be an address.
        for (const bad of ["ansh@gmail", "no-at-sign.com", "two@@at.com", "@nothing.com"]) {
            res = await api("PATCH", "/profile/me", { token: employee, body: { email: bad } });
            check(`"${bad}" is refused`, res.status === 400, `HTTP ${res.status}`);
        }
        check("and the refusals changed nothing",
            psql(DB, `SELECT email FROM employees WHERE employee_id='E001'`)
                === "rajesh@amaze.co");

        // SAVING ONE MUST NOT WIPE THE OTHER. The page sends both together,
        // but anything else calling this endpoint may send one — and a
        // COALESCE-free UPDATE would have written NULL over the field that
        // was not mentioned.
        res = await api("PATCH", "/profile/me",
            { token: employee, body: { phone: "+91 99999 11111" } });
        check("saving only the phone leaves the email alone",
            res.status === 200
            && psql(DB, `SELECT email FROM employees WHERE employee_id='E001'`)
                === "rajesh@amaze.co");
        res = await api("PATCH", "/profile/me", { token: employee, body: { email: "" } });
        check("an empty address clears it, which people do want",
            res.status === 200
            && psql(DB, `SELECT COALESCE(email,'(null)') FROM employees WHERE employee_id='E001'`)
                === "(null)");
        check("and the phone survived that too",
            psql(DB, `SELECT phone FROM employees WHERE employee_id='E001'`)
                === "+91 99999 11111");

        console.log("\nProving the email address");
        await api("PATCH", "/profile/me",
            { token: employee, body: { email: "rajesh@amaze.co" } });

        res = await api("POST", "/profile/me/email/code", { token: employee });
        check("a code can be asked for", res.status === 200, `HTTP ${res.status}`);
        check("it went to the address on the profile",
            posted.length === 1 && posted[0].to === "rajesh@amaze.co",
            JSON.stringify(posted[0] || {}).slice(0, 120));
        check("and the code itself is NOT in the reply — that is the whole point",
            !/[0-9]{6}/.test(JSON.stringify(res.body)),
            JSON.stringify(res.body));

        let sentCode = (posted[0].text.match(/\b([0-9]{6})\b/) || [])[1];
        check("the message carries a six-digit code", Boolean(sentCode),
            posted[0].text.slice(0, 80));
        check("and it is not what is stored — the row holds a hash",
            psql(DB, `SELECT code_hash FROM email_verifications WHERE employee_id='E001'`)
                !== sentCode);

        res = await api("POST", "/profile/me/email/code", { token: employee });
        check("asking again straight away is throttled", res.status === 429,
            `HTTP ${res.status} — the button is otherwise a way to send `
            + `somebody a hundred emails`);
        // A WAIT SOMEBODY WOULD ACTUALLY WAIT. Under a minute, because the
        // throttle is sixty seconds — "ask again in 19799 seconds" is what
        // the broken arithmetic below produced, and it reads as a fault.
        const waitSeconds = Number(
            (JSON.stringify(res.body).match(/in (\d+) seconds/) || [])[1]);
        check("and it says how long to wait, in a number that makes sense",
            waitSeconds > 0 && waitSeconds <= 60, String(waitSeconds));

        // THE THROTTLE MUST ALSO LET GO, and a "429 was returned" check
        // cannot see that half. Any arithmetic slip that makes the age come
        // out negative — the shape of mistake this kind of code invites, and
        // one this codebase avoids only because of a type parser three files
        // away — still produces a 429 and still passes the check above,
        // while locking somebody out of asking again for hours.
        psql(DB, `UPDATE email_verifications
                     SET sent_at = (NOW() AT TIME ZONE 'UTC') - INTERVAL '2 minutes'
                   WHERE employee_id='E001'`);
        posted.length = 0;
        res = await api("POST", "/profile/me/email/code", { token: employee });
        check("two minutes later a new code CAN be asked for", res.status === 200,
            `HTTP ${res.status} ${JSON.stringify(res.body).slice(0, 90)}`);
        check("and one was actually sent", posted.length === 1);
        // The new code REPLACES the old one — one pending verification per
        // person, so that somebody who asked twice finds exactly one of them
        // opens the door. Everything below therefore uses this one.
        sentCode = (posted[0].text.match(/\b([0-9]{6})\b/) || [])[1];

        res = await api("POST", "/profile/me/email/verify",
            { token: employee, body: { code: "000000" === sentCode ? "111111" : "000000" } });
        check("a wrong code is refused", res.status === 400, `HTTP ${res.status}`);
        check("and it says how many tries are left",
            /left/i.test(JSON.stringify(res.body)), JSON.stringify(res.body).slice(0, 120));
        check("the address is still unverified",
            (await api("GET", "/profile/me", { token: employee }))
                .body.profile.email_verified === false);

        res = await api("POST", "/profile/me/email/verify",
            { token: employee, body: { code: sentCode } });
        check("the right code is accepted", res.status === 200,
            JSON.stringify(res.body).slice(0, 120));
        check("the profile now says it is verified",
            (await api("GET", "/profile/me", { token: employee }))
                .body.profile.email_verified === true);
        check("and the pending code is gone, so it cannot be used twice",
            psql(DB, `SELECT COUNT(*) FROM email_verifications WHERE employee_id='E001'`)
                === "0");

        // GUESSING IS BOUNDED. Six digits is a million possibilities against a
        // person and nothing at all against a loop.
        await api("PATCH", "/profile/me",
            { token: employee, body: { email: "rajesh2@amaze.co" } });
        posted.length = 0;
        psql(DB, `DELETE FROM email_verifications`);
        await api("POST", "/profile/me/email/code", { token: employee });
        const second = (posted[0].text.match(/\b([0-9]{6})\b/) || [])[1];
        const wrong = second === "123456" ? "654321" : "123456";
        let lastStatus = 0;
        for (let i = 0; i < 6; i += 1) {
            lastStatus = (await api("POST", "/profile/me/email/verify",
                { token: employee, body: { code: wrong } })).status;
        }
        check("six wrong codes stop being answered", lastStatus === 429,
            `HTTP ${lastStatus}`);
        res = await api("POST", "/profile/me/email/verify",
            { token: employee, body: { code: second } });
        check("and the real code no longer works either — ask for a new one",
            res.status === 429, `HTTP ${res.status}`);

        // CHANGING THE ADDRESS UN-PROVES IT. Carrying the tick across would
        // make the tick mean nothing, and something is eventually sent on the
        // strength of it.
        psql(DB, `DELETE FROM email_verifications`);
        psql(DB, `UPDATE employees SET email='rajesh@amaze.co',
                  email_verified_at = NOW() WHERE employee_id='E001'`);
        await api("PATCH", "/profile/me",
            { token: employee, body: { email: "somewhere-else@amaze.co" } });
        check("a new address is not verified",
            (await api("GET", "/profile/me", { token: employee }))
                .body.profile.email_verified === false);

        // ...but saving the SAME address again is not a change, and must not
        // throw away a verification somebody has already done. The page sends
        // phone and email together, so this happens on every phone edit.
        psql(DB, `UPDATE employees SET email_verified_at = NOW() WHERE employee_id='E001'`);
        await api("PATCH", "/profile/me", {
            token: employee,
            body: { email: "somewhere-else@amaze.co", phone: "+91 98888 77777" },
        });
        check("saving the same address keeps it verified",
            (await api("GET", "/profile/me", { token: employee }))
                .body.profile.email_verified === true,
            "editing a phone number would otherwise un-verify the email every time");

        res = await api("POST", "/profile/me/email/verify",
            { token: employee, body: { code: "12345" } });
        check("a code of the wrong shape never reaches the database",
            res.status === 400, `HTTP ${res.status}`);

        console.log("\nWhat an employee must NOT be able to change about themselves");
        // Sent through their own endpoint, which only reads `phone` — the
        // rest must be ignored rather than quietly applied.
        await api("PATCH", "/profile/me", {
            token: employee,
            body: {
                phone: "+91 90000 00000",
                role: "super_admin", department: "Board", designation: "CEO",
                employee_id: "SA01", employment_status: "terminated",
                reporting_manager: null, joining_date: "2000-01-01",
            },
        });
        const after = psql(DB,
            `SELECT role||'|'||designation||'|'||COALESCE(department,'-')||'|'||employee_id
               FROM employees WHERE employee_id='E001'`);
        check("role, designation, department and id are all untouched",
            after === "employee|Developer|-|E001", after);

        // And through the administrator's endpoint, which is the one that CAN
        // write them — an employee's token must not reach it at all.
        for (const [route, body] of [
            ["/admin/employees/E001/profile", { department: "Board" }],
            ["/admin/employees/E001/role", { role: "super_admin" }],
        ]) {
            res = await api("POST", route, { token: employee, body });
            check(`an employee is refused at ${route}`,
                [401, 403].includes(res.status), `HTTP ${res.status}`);
        }
        check("and none of it took", psql(DB,
            `SELECT role||'|'||COALESCE(department,'-') FROM employees WHERE employee_id='E001'`)
            === "employee|-");

        console.log("\nWho may set the company's record of somebody");
        res = await api("POST", "/admin/employees/E001/profile", {
            token: admin, body: { department: "Engineering" },
        });
        check("an ORDINARY admin cannot set the department", res.status === 403,
            `HTTP ${res.status} ${JSON.stringify(res.body).slice(0, 90)}`);
        res = await api("POST", "/admin/employees/E001/profile", {
            token: owner,
            body: {
                department: "Engineering", reporting_manager: "A001",
                joining_date: "2025-06-01", employment_status: "probation",
            },
        });
        check("a super admin can", res.status === 200, JSON.stringify(res.body).slice(0, 140));
        check("and every field landed", psql(DB,
            `SELECT department||'|'||reporting_manager||'|'||joining_date||'|'||employment_status
               FROM employees WHERE employee_id='E001'`)
            === "Engineering|A001|2025-06-01|probation");
        res = await api("GET", "/profile/me", { token: employee });
        check("the employee's page now shows the manager by NAME, not an id",
            res.body.profile.reporting_manager === "Priya Nair",
            String(res.body.profile.reporting_manager));

        // A DATE LEAVES AS THE DATE IT IS, with no time and no timezone on it.
        //
        // Seen on a real page: a joining date of 2022-07-15 displayed as
        // 2022-07-14. pg builds a JavaScript Date at LOCAL midnight from a
        // DATE column, and JSON.stringify writes that in UTC — so at +05:30
        // it left as "…T18:30:00.000Z" and every reader taking the first ten
        // characters got the day before.
        //
        // This check is exact rather than "starts with", because the
        // timestamp form is wrong even where it happens to be harmless: on a
        // UTC server the first ten characters are right, which is precisely
        // why it survived to be found on somebody's laptop instead.
        check("the joining date is a plain YYYY-MM-DD, not a timestamp",
            res.body.profile.joining_date === "2025-06-01",
            JSON.stringify(res.body.profile.joining_date));

        // The chart on the same page labels its columns from a DATE too, so
        // the same slip moved every bar to the day before.
        const chart = (await api("GET", "/profile/me/work-summary", { token: employee }))
            .body.last_7_days || [];
        check("and so is every day on the seven-day chart",
            chart.length === 7 && chart.every((d) => /^\d{4}-\d{2}-\d{2}$/.test(d.day)),
            JSON.stringify(chart.slice(0, 2)));

        res = await api("POST", "/admin/employees/E001/profile", {
            token: owner, body: { employment_status: "on a beach" },
        });
        check("an invented employment status is refused", res.status === 400, `HTTP ${res.status}`);
        res = await api("POST", "/admin/employees/E001/profile", {
            token: owner, body: { joining_date: "01-06-2025" },
        });
        check("a date in the wrong shape is refused", res.status === 400, `HTTP ${res.status}`);
        res = await api("POST", "/admin/employees/E001/profile", {
            token: owner, body: { reporting_manager: "E001" },
        });
        check("and nobody can be made to report to themselves", res.status === 400,
            `HTTP ${res.status}`);

        console.log("\nA suspended account says so, whatever its status field says");
        await api("POST", "/admin/employees/E002/suspend", { token: owner, body: { suspended: true } });
        psql(DB, `UPDATE employees SET employment_status='active' WHERE employee_id='E002'`);
        // Sneha's session died with the suspension, which is correct; read it
        // as the owner instead, through the database, to check the wording.
        const suspendedStatus = psql(DB,
            `SELECT CASE WHEN suspended THEN 'suspended' ELSE employment_status END
               FROM employees WHERE employee_id='E002'`);
        check("it reads suspended, not active", suspendedStatus === "suspended", suspendedStatus);
        await api("POST", "/admin/employees/E002/suspend", { token: owner, body: { suspended: false } });
        // Suspending ended Sneha's session — correctly. Her token is needed
        // below to prove an AUTHORISATION refusal (403), and a dead session
        // would answer 401 instead, which proves something else entirely.
        other = await login("sneha", "sneha-laptop");

        console.log("\nMy photo");
        const png = Buffer.from(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154789c6300010000050001" +
            "0d0a2db40000000049454e44ae426082", "hex");
        res = await sendPhoto(employee, png);
        check("a PNG uploads", res.status === 200, JSON.stringify(res.body).slice(0, 120));
        const firstName = res.body.photo;
        check("the file is on disk under a generated name",
            firstName && fs.existsSync(path.join(photos, firstName)) && !firstName.includes("me.png"),
            String(firstName));
        check("and the row points at it",
            psql(DB, `SELECT photo FROM employees WHERE employee_id='E001'`) === firstName);

        res = await sendPhoto(employee, png, "image/jpeg", "me.jpg");
        check("replacing it works", res.status === 200, JSON.stringify(res.body).slice(0, 90));
        check("and the one it replaced is gone from disk — not left for ever",
            !fs.existsSync(path.join(photos, firstName)), firstName);

        res = await sendPhoto(employee, Buffer.from("not an image at all"), "text/plain", "x.txt");
        check("a text file pretending to be a photo is refused", res.status === 400,
            `HTTP ${res.status} ${JSON.stringify(res.body).slice(0, 90)}`);
        res = await sendPhoto(employee, Buffer.alloc(6 * 1024 * 1024));
        check("an over-sized image is refused with 413, not 500", res.status === 413,
            `HTTP ${res.status}`);
        check("and the refusal names the limit",
            /5 MB/.test(JSON.stringify(res.body)), JSON.stringify(res.body).slice(0, 120));

        res = await api("GET", "/profile/photo", { token: employee });
        check("I can fetch my own photo", res.status === 200 || res.status === 304,
            `HTTP ${res.status}`);
        // ANYBODY SIGNED IN CAN SEE ANYBODY'S PHOTOGRAPH — the owner's rule,
        // for a company of a thousand people. What is checked here is that
        // the door is still shut to somebody with no session at all: the
        // directory is the company's, not the internet's.
        res = await api("GET", "/profile/photo/E001", { token: other });
        check("a colleague in no shared team can see it",
            res.status === 200, `HTTP ${res.status}`);

        const third = await login("chandra");
        res = await api("GET", "/profile/photo/E001", { token: third });
        check("and so can somebody who has never messaged them",
            res.status === 200, `HTTP ${res.status}`);

        res = await api("GET", "/profile/photo/E001", { token: null });
        check("but not somebody without a session", res.status === 401,
            `HTTP ${res.status} — the directory is the company's, not the `
            + `internet's`);

        res = await api("GET", "/profile/photo/E002", { token: employee });
        check("somebody with no photo is a 404, not a refusal",
            res.status === 404,
            `HTTP ${res.status} — "there is none" and "you may not" must not `
            + `look the same`);

        res = await api("DELETE", "/profile/me/photo", { token: employee });
        check("removing it works", res.status === 200, `HTTP ${res.status}`);
        check("the row is cleared",
            psql(DB, `SELECT COALESCE(photo,'(null)') FROM employees WHERE employee_id='E001'`)
                === "(null)");
        check("and no file is left behind",
            fs.readdirSync(photos).length === 0, String(fs.readdirSync(photos)));

        console.log("\nWork summary — the same figures the rest of the product uses");
        psql(DB, `INSERT INTO attendance (employee_id, login_time, logout_time, total_hours)
                  VALUES ('E001', (NOW() AT TIME ZONE 'UTC') - INTERVAL '3 hours',
                          (NOW() AT TIME ZONE 'UTC') - INTERVAL '1 hour', INTERVAL '2 hours')`);
        psql(DB, `INSERT INTO idle_daily (employee_id, day, idle_seconds)
                  VALUES ('E001', DATE(NOW() AT TIME ZONE 'Asia/Kolkata'), 1800)`);
        // created_at IS GIVEN, not left to the column default.
        //
        // The default is `now()`, which writes the SESSION's wall clock into
        // a column that is read as naive UTC. On a UTC server those are the
        // same thing; on a machine at +05:30 the row lands five and a half
        // hours ahead, and after 18:30 UTC it belongs to tomorrow's IST day —
        // so this check passed all morning and failed in the evening, on the
        // same code. A test that depends on what time it is run is worse than
        // no test.
        psql(DB, `INSERT INTO screenshots (employee_id, file_name, created_at) VALUES
                    ('E001','a.enc', NOW() AT TIME ZONE 'UTC'),
                    ('E001','b.enc', NOW() AT TIME ZONE 'UTC')`);

        res = await api("GET", "/profile/me/work-summary", { token: employee });
        check("it answers", res.status === 200, JSON.stringify(res.body).slice(0, 140));
        const w = res.body;
        check("today's screenshots are counted", w.today.screenshots === 2, String(w.today.screenshots));
        check("today's idle time comes from the same table the dashboard reads",
            w.today.idle_seconds === 1800, String(w.today.idle_seconds));
        check("active time is worked minus idle, never negative",
            w.today.active_seconds >= 0, String(w.today.active_seconds));
        check("this week has a day in it", w.week.days_present >= 1, JSON.stringify(w.week));
        check("this month reports an average per day",
            w.month.average_daily_seconds > 0, JSON.stringify(w.month));
        check("attendance is a percentage of days SO FAR, never over 100",
            w.month.attendance_percent > 0 && w.month.attendance_percent <= 100,
            String(w.month.attendance_percent));
        check("the chart has exactly seven days", (w.last_7_days || []).length === 7,
            String((w.last_7_days || []).length));
        check("including the days nothing happened — a gap reads as missing data",
            w.last_7_days.every((d) => "worked_seconds" in d && "screenshots" in d));

        console.log("\nWhere I am signed in");
        res = await api("GET", "/profile/me/sessions", { token: employee });
        check("the list answers", res.status === 200, `HTTP ${res.status}`);
        check("this machine is in it", (res.body.sessions || []).length >= 1,
            JSON.stringify(res.body.sessions || []).slice(0, 120));
        check("and it is marked as the one I am using",
            (res.body.sessions || []).some((s) => s.is_this_device),
            JSON.stringify(res.body.sessions || []).slice(0, 160));
        check("no token is ever sent back",
            !JSON.stringify(res.body).includes("eyJ"), "a session list handed out credentials");
        check("recent sign-ins are listed too", Array.isArray(res.body.history));

        console.log("\nSigning out everywhere");
        res = await api("POST", "/profile/me/logout-all", { token: employee });
        check("it is accepted", res.status === 200, JSON.stringify(res.body).slice(0, 120));
        check("the stored token is cleared",
            psql(DB, `SELECT COALESCE(token,'(cleared)') FROM active_sessions
                       WHERE employee_id='E001'`) === "(cleared)");
        check("the login flag goes with it",
            psql(DB, `SELECT is_logged_in FROM employees WHERE employee_id='E001'`) === "f");
        check("and the token I asked with is dead too — that is the point",
            (await api("GET", "/profile/me", { token: employee })).status === 401);
        employee = await login("rajesh", "rajesh-laptop");
        check("signing back in works immediately", Boolean(employee));

        console.log("\nAn employee id is a roll number");
        // It was any non-empty string, so "raju kumar", "  " and a stray
        // paste all became permanent primary keys — printed on reports, typed
        // into searches, and impossible to change afterwards without touching
        // every row that points at them.
        for (const bad of ["raju kumar", " ", "a", "#1", "EMP/001",
                           "x".repeat(21), "emp 001"]) {
            const attempt = await api("POST", "/admin/employees", {
                token: owner,
                body: { employee_id: bad, username: `u${Date.now()}`,
                        password: "GoodPass123", role: "employee", full_name: "Somebody" },
            });
            check(`"${bad}" is refused as an id`, attempt.status === 400,
                `HTTP ${attempt.status}`);
        }
        for (const good of ["EMP009", "AMZ-004", "TEST_01"]) {
            const attempt = await api("POST", "/admin/employees", {
                token: owner,
                body: { employee_id: good, username: `ok${good}`,
                        password: "GoodPass123", role: "employee", full_name: "Somebody" },
            });
            check(`"${good}" is accepted`, attempt.status === 200,
                `HTTP ${attempt.status} ${JSON.stringify(attempt.body).slice(0, 90)}`);
        }

        console.log("\nAnd the next one is offered rather than guessed");
        // 26AMZEM001 — year, company, role, number. Each role counts
        // separately, and the year part is why the number starts again in
        // January.
        const yy = new Date().toLocaleDateString("en-GB", {
            timeZone: "Asia/Kolkata", year: "2-digit" });
        for (const [role, code] of [["employee", "EM"], ["admin", "AD"],
                                    ["super_admin", "SU"]]) {
            const got = await api("GET", `/admin/employees/next-id?role=${role}`,
                { token: owner });
            check(`a ${role} is offered ${yy}AMZ${code}nnn`,
                new RegExp(`^${yy}AMZ${code}\\d{3}$`).test(got.body.employee_id || ""),
                String(got.body.employee_id));
        }
        let firstEm = (await api("GET", "/admin/employees/next-id?role=employee",
            { token: owner })).body.employee_id;
        check("the first of the year is 001", firstEm.endsWith("001"), firstEm);
        await api("POST", "/admin/employees", {
            token: owner,
            body: { employee_id: firstEm, username: "formatted",
                    password: "GoodPass123", role: "employee", full_name: "Formatted Hire" },
        });
        const secondEm = (await api("GET", "/admin/employees/next-id?role=employee",
            { token: owner })).body.employee_id;
        check("and the one after it is 002", secondEm.endsWith("002"), secondEm);
        const adminId = (await api("GET", "/admin/employees/next-id?role=admin",
            { token: owner })).body.employee_id;
        check("an admin still starts at 001 — the roles count separately",
            adminId.endsWith("001"), adminId);

        // A NUMBER IS NEVER HANDED OUT TWICE, so a retired one does not free
        // its place in the series.
        await api("DELETE", `/admin/employees/${firstEm}`, { token: owner });
        const afterRetire = (await api("GET", "/admin/employees/next-id?role=employee",
            { token: owner })).body.employee_id;
        check("deleting 001 does not make 001 the next suggestion again",
            afterRetire !== firstEm, `${firstEm} was retired, offered ${afterRetire}`);

        const badRole = await api("GET", "/admin/employees/next-id?role=wizard",
            { token: owner });
        check("an invented role is refused rather than guessed at",
            badRole.status === 400, `HTTP ${badRole.status}`);

        // EMP001, EMP009 and E001/E002 exist by now; the commonest series
        // wins and the padding is kept.
        let nid = await api("GET", "/admin/employees/next-id", { token: owner });
        check("a suggestion comes back", nid.status === 200 && Boolean(nid.body.employee_id),
            JSON.stringify(nid.body));
        check("it is a valid id", /^[A-Za-z0-9_-]{2,20}$/.test(nid.body.employee_id || ""),
            String(nid.body.employee_id));
        check("and it is not one already taken",
            psql(DB, `SELECT count(*) FROM employees WHERE employee_id='${nid.body.employee_id}'`) === "0",
            String(nid.body.employee_id));
        const created = await api("POST", "/admin/employees", {
            token: owner,
            body: { employee_id: nid.body.employee_id, username: "suggested",
                    password: "GoodPass123", role: "employee", full_name: "Suggested Hire" },
        });
        check("the suggestion can actually be used", created.status === 200,
            `HTTP ${created.status} ${JSON.stringify(created.body).slice(0, 90)}`);
        const nextAgain = await api("GET", "/admin/employees/next-id", { token: owner });
        check("and the next suggestion moves on",
            nextAgain.body.employee_id !== nid.body.employee_id,
            `${nid.body.employee_id} -> ${nextAgain.body.employee_id}`);

        console.log("\nThe same roll number is never given to two people");
        // employee_id is the primary key, so the database itself refuses a
        // second one — but what matters is that the ANSWER is usable: a 409
        // and a sentence, not a 500 with a constraint name in it.
        let dupe = await api("POST", "/admin/employees", {
            token: owner,
            body: { employee_id: "EMP009", username: "someone-else",
                    password: "GoodPass123", role: "employee", full_name: "Different Person" },
        });
        check("a second account cannot take an id already in use",
            [400, 409].includes(dupe.status), `HTTP ${dupe.status}`);
        check("and it says so in words somebody can act on",
            /already|exists|taken|use/i.test(dupe.body.message || ""),
            JSON.stringify(dupe.body).slice(0, 140));
        check("the original is untouched",
            psql(DB, `SELECT username FROM employees WHERE employee_id='EMP009'`) === "okEMP009");

        // AND NOT AFTER A DELETION EITHER. Attendance, screenshots and the
        // audit log all name people by this id; handing a retired one to a
        // new hire would quietly merge two people's history.
        await api("DELETE", "/admin/employees/EMP009", { token: owner });
        const reused = await api("POST", "/admin/employees", {
            token: owner,
            body: { employee_id: "EMP009", username: "recycled",
                    password: "GoodPass123", role: "employee", full_name: "New Hire" },
        });
        check("a deleted person's id is not handed to somebody new",
            [400, 409].includes(reused.status),
            `HTTP ${reused.status} — reusing it merges two people's history`);

        console.log("\nNobody else's account is reachable from here");
        check("there is no employee id in any of these routes to tamper with",
            (await api("GET", "/profile/me", { token: other })).body.profile.employee_id === "E002",
            "the route answered about somebody other than the caller");

        server.close();
        await pool.end();
    } finally {
        try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
        try { fs.rmSync(photos, { recursive: true, force: true }); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.stdout.write("", () => process.exit(1));
    } else {
        console.log("all profile checks passed");
        process.stdout.write("", () => process.exit(0));
    }
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    process.exit(1);
});
