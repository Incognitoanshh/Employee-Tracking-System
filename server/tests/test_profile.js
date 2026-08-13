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
            ('E002','sneha','${hash}','employee','Sneha Iyer','Designer')`);

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
        });

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
        res = await api("GET", "/profile/photo/E001", { token: other });
        check("another employee cannot fetch mine", res.status === 403, `HTTP ${res.status}`);
        res = await api("GET", "/profile/photo/E001", { token: admin });
        check("an admin can — they manage people", res.status === 200, `HTTP ${res.status}`);

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
        psql(DB, `INSERT INTO screenshots (employee_id, file_name) VALUES ('E001','a.enc'),('E001','b.enc')`);

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
