/**
 * Hiring somebody: the fields a payroll needs, and the login they may not get.
 *
 * ── THE HALF THAT MATTERS MOST ──────────────────────────────────────────
 *
 * `username` and `password` became NULLABLE so that an employee can be on the
 * payroll without being able to sign in — a contractor, or anybody paid who
 * never runs the desktop client. Relaxing a NOT NULL on the table
 * authentication reads from is the kind of change that has to be proved in
 * both directions, so this file proves both:
 *
 *   1. an employee with no username CANNOT sign in, by any spelling of their
 *      name, id, or an empty string;
 *   2. everybody else signs in exactly as they did before.
 *
 * The safety is SQL's, not a check anyone has to remember: login is
 * `WHERE LOWER(username) = LOWER($1)`, and LOWER(NULL) = anything is NULL,
 * never true. That is what makes it worth testing rather than trusting — a
 * property of the query is invisible in a diff that changes the query.
 *
 * ── THE REST ────────────────────────────────────────────────────────────
 *
 * The onboarding fields are all optional, because a person can be hired before
 * their PAN is handed over, and a form that will not save until every box is
 * full is a form that gets filled with rubbish. What they are NOT is
 * unchecked: a malformed IFSC does not bounce politely, it fails at the bank
 * days later, by which time payday has passed.
 *
 * Run:  node server/tests/test_employee_onboarding.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_onboard_${process.pid}`;
const PORT = 8000 + ((process.pid + 451) % 1000);
const BASE = `http://127.0.0.1:${PORT}/api`;

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
        ...(body ? { body: JSON.stringify(body) } : {}),
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    return { status: response.status, body: payload };
}

async function main() {
    const root = path.resolve(__dirname, "..", "..");
    console.log("\nHiring somebody\n");

    migrate(DB);

    const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
    const seeded = await bcrypt.hash("SuperSecret123", 10);
    psql(DB, `INSERT INTO employees (employee_id, username, password, role, full_name, created_at)
              VALUES ('SA001','superadmin','${seeded}','super_admin','Owner','2026-01-01'),
                     ('E001','asha','${seeded}','employee','Asha','2026-01-01')`);

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

    const token = (await api("POST", "/auth/login",
        { body: { username: "superadmin", password: "SuperSecret123" } })).body.token;

    const login = (username, password) =>
        api("POST", "/auth/login", { body: { username, password } });

    // ── the shape that existed before this change still works ───────────
    //
    // FIRST, and deliberately. Everything below relaxes a rule; if the old
    // six-field call has stopped behaving, nothing else here is worth reading.
    let res = await api("POST", "/admin/employees", {
        token,
        body: { employee_id: "E100", username: "raju", password: "GoodPass123",
                role: "employee", full_name: "Raju Kumar" },
    });
    check("the old six-field create still works", res.status === 200,
        `${res.status} ${JSON.stringify(res.body).slice(0, 120)}`);
    res = await login("raju", "GoodPass123");
    check("and that employee can sign in", res.status === 200 && !!res.body.token,
        `${res.status} ${res.body.message || ""}`);

    // The seeded accounts, which predate the migration entirely. superadmin is
    // not re-tried here: this test signed in as them at the top, and the app
    // allows one session per account, so a second attempt is refused for
    // being already signed in — which proves nothing about the migration and
    // would fail for the right reason at the wrong moment.
    check("superadmin, who existed before the migration, signed in above",
        !!token, "no token from the first login");
    res = await login("asha", "SuperSecret123");
    check("and asha, who also predates it, signs in now",
        res.status === 200 && !!res.body.token,
        `${res.status} ${res.body.message || ""}`);

    // A WRONG PASSWORD IS STILL WRONG. Relaxing NOT NULL must not turn the
    // comparison into something that passes on an absent value.
    res = await login("asha", "NotHerPassword1");
    check("but a wrong password is still refused",
        res.status !== 200 && !res.body.token, `HTTP ${res.status}`);

    // ── an employee with no login at all ────────────────────────────────
    res = await api("POST", "/admin/employees", {
        token,
        body: {
            employee_id: "C001", role: "employee", full_name: "Meera Contractor",
            portal_access: false,
            joining_date: "2026-08-01", department: "Design",
            email: "meera@amazeinternet.com", phone: "9876543210",
            gender: "female", work_location: "Remote",
            date_of_birth: "1994-03-12", pan: "abcde1234f",
            address: "14 MG Road, Bengaluru",
            payment_mode: "bank_transfer", bank_name: "HDFC Bank",
            bank_account_number: "0123 4567 8901", bank_ifsc: "hdfc0001234",
        },
    });
    check("an employee can be added with no portal access", res.status === 200,
        `${res.status} ${JSON.stringify(res.body).slice(0, 160)}`);

    const stored = psql(DB, `SELECT COALESCE(username,'<null>') || '|'
                                  || COALESCE(password,'<null>')
                               FROM employees WHERE employee_id = 'C001'`);
    check("stored with no username and no password", stored === "<null>|<null>",
        stored);

    // ── THE ONE THAT MATTERS: they cannot get in ────────────────────────
    //
    // Every spelling somebody might have, plus the empty string, which is the
    // one an attacker would actually try against a nullable column.
    for (const [attempt, label] of [
        ["", "an empty username"],
        [" ", "a blank username"],
        ["C001", "their employee id"],
        ["Meera Contractor", "their full name"],
        ["meera", "a guess at a username"],
        ["null", "the word null"],
        ["NULL", "the word NULL"],
    ]) {
        const got = await login(attempt, "GoodPass123");
        check(`${label} does not sign them in`, got.status !== 200 && !got.body.token,
            `HTTP ${got.status} token=${!!got.body.token}`);
    }
    // And with no password either — the route's own guard, before the query.
    const blank = await login("", "");
    check("neither does an empty username and password",
        blank.status !== 200 && !blank.body.token, `HTTP ${blank.status}`);

    // A SECOND ONE. NULLs do not collide in a unique index, so two
    // credential-less employees must both be allowed to exist.
    res = await api("POST", "/admin/employees", {
        token, body: { employee_id: "C002", role: "employee",
                       full_name: "Second Contractor", portal_access: false },
    });
    check("a second employee with no login is allowed too", res.status === 200,
        `${res.status} ${JSON.stringify(res.body).slice(0, 120)}`);

    // ── the fields a payroll needs ──────────────────────────────────────
    const row = JSON.parse(psql(DB,
        `SELECT row_to_json(t) FROM (
            SELECT gender, work_location, TO_CHAR(date_of_birth,'YYYY-MM-DD') AS dob,
                   pan, address, payment_mode, bank_name,
                   bank_account_number, bank_ifsc, department, email, phone,
                   TO_CHAR(joining_date,'YYYY-MM-DD') AS joined
              FROM employees WHERE employee_id = 'C001') t`));
    // ── THE FOUR THAT WERE BEING THROWN AWAY ────────────────────────────
    //
    // employees has carried email, phone, department and joining_date for a
    // long time, and the employee page has a row for each — but createEmployee
    // wrote six columns and ignored the rest of the body. So somebody added
    // with a full record arrived with a designation and nothing else, and
    // every one of those rows read "—". It had to be set a SECOND time,
    // through the edit dialog, which is a different code path and did save
    // them. Reported as exactly that: "create karte time set kiya to dobara
    // set karna hi kyun pad raha hai".
    check("the work email is kept", row.email === "meera@amazeinternet.com",
        String(row.email));
    check("the phone number is kept", row.phone === "9876543210",
        String(row.phone));
    check("the department is kept", row.department === "Design",
        String(row.department));
    check("and the joining date, as the date it was given",
        row.joined === "2026-08-01", String(row.joined));

    check("gender, work location and date of birth are kept",
        row.gender === "female" && row.work_location === "Remote"
        && row.dob === "1994-03-12", JSON.stringify(row));
    check("the address is kept", row.address === "14 MG Road, Bengaluru", row.address);
    // UPPER CASED ON THE WAY IN. Two casings of one PAN are one PAN, and only
    // one of them can go on a filing.
    check("a PAN typed in lower case is stored upper case",
        row.pan === "ABCDE1234F", row.pan);
    check("and so is an IFSC", row.bank_ifsc === "HDFC0001234", row.bank_ifsc);
    // Spaces are how account numbers are written down and never how they are
    // stored — a transfer to "0123 4567 8901" is not a transfer.
    check("spaces come out of the account number",
        row.bank_account_number === "012345678901", row.bank_account_number);
    check("the payment mode is kept", row.payment_mode === "bank_transfer",
        row.payment_mode);

    // ── what is refused ─────────────────────────────────────────────────
    const bad = [
        ["pan", "ABCD1234F", "a PAN of the wrong shape"],
        ["pan", "12345ABCDE", "a PAN with the letters and digits swapped"],
        ["bank_ifsc", "HDFC1001234", "an IFSC without the zero"],
        ["bank_ifsc", "HD0001234", "an IFSC that is too short"],
        ["bank_account_number", "12345", "an account number that is too short"],
        ["bank_account_number", "12345678901234567890123", "one that is too long"],
        ["bank_account_number", "12345A6789", "one with a letter in it"],
        ["gender", "helicopter", "a gender that is not on the list"],
        ["payment_mode", "bitcoin", "a payment mode nobody handles"],
        ["date_of_birth", "12/03/1994", "a date in the wrong format"],
        ["date_of_birth", "not-a-date", "a date that is not one"],
    ];
    for (const [field, value, label] of bad) {
        const attempt = await api("POST", "/admin/employees", {
            token,
            body: { employee_id: `X${Math.random().toString(36).slice(2, 8)}`,
                    username: `u${Math.random().toString(36).slice(2, 8)}`,
                    password: "GoodPass123", role: "employee",
                    full_name: "Somebody", [field]: value },
        });
        check(`${label} is refused`, attempt.status === 400,
            `HTTP ${attempt.status} ${attempt.body.message || ""}`);
        if (attempt.status === 400) {
            check(`  …and says which field`, attempt.body.field === field,
                `field=${attempt.body.field}`);
        }
    }

    // ── a blank optional field is absent, not empty ─────────────────────
    //
    // "" and "not given" are the same fact, and only one of them sorts,
    // filters and prints as a dash.
    res = await api("POST", "/admin/employees", {
        token,
        body: { employee_id: "E101", username: "blanks", password: "GoodPass123",
                role: "employee", full_name: "Blank Fields",
                pan: "", address: "   ", gender: "", bank_ifsc: "" },
    });
    check("blank optional fields are accepted", res.status === 200,
        `${res.status} ${JSON.stringify(res.body).slice(0, 120)}`);
    const blanks = psql(DB, `SELECT COALESCE(pan,'<null>') || '|'
                                  || COALESCE(address,'<null>') || '|'
                                  || COALESCE(gender,'<null>')
                               FROM employees WHERE employee_id = 'E101'`);
    check("and stored as nothing, not as empty text",
        blanks === "<null>|<null>|<null>", blanks);

    // ── an admin always has a login ─────────────────────────────────────
    for (const role of ["admin", "super_admin"]) {
        const attempt = await api("POST", "/admin/employees", {
            token, body: { employee_id: `NO${role.slice(0, 3)}`, role,
                           full_name: "No Login", portal_access: false },
        });
        check(`a ${role} cannot be created without portal access`,
            attempt.status === 400, `HTTP ${attempt.status}`);
    }

    // ── the list filters, applied to the rows and not to the page ───────
    //
    // A filter the panel applies to the fifty rows it is holding answers
    // "which of these fifty are in Design", which is not the question, and
    // leaves the total underneath reading the unfiltered count. So each of
    // these checks the TOTAL as well as the rows: the total is what tells
    // somebody whether they are looking at everybody.
    psql(DB, `UPDATE employees SET department = 'Design'
               WHERE employee_id IN ('C001','C002')`);
    psql(DB, `UPDATE employees SET department = 'Engineering'
               WHERE employee_id IN ('E001','E100')`);
    psql(DB, `UPDATE employees SET suspended = TRUE WHERE employee_id = 'E100'`);

    const list = async (query = "") =>
        (await api("GET", `/admin/employees${query}`, { token })).body;

    const everyone = await list();
    check("unfiltered, the list is everybody",
        everyone.total === Number(psql(DB, "SELECT COUNT(*) FROM employees")),
        `${everyone.total} vs ${psql(DB, "SELECT COUNT(*) FROM employees")}`);

    const design = await list("?department=Design");
    check("a department filter returns only that department",
        design.data.every((r) => r.department === "Design"),
        JSON.stringify(design.data.map((r) => r.department)));
    check("and the total is the filtered count, not the whole company",
        design.total === 2, `total=${design.total}`);

    const admins = await list("?role=super_admin");
    check("a role filter returns only that role",
        admins.data.every((r) => r.role === "super_admin") && admins.total === 1,
        `${admins.total}: ${JSON.stringify(admins.data.map((r) => r.role))}`);

    const suspended = await list("?status=suspended");
    check("status=suspended returns the suspended ones",
        suspended.total === 1 && suspended.data[0]?.employee_id === "E100",
        JSON.stringify(suspended.data.map((r) => r.employee_id)));
    const active = await list("?status=active");
    check("and status=active leaves them out",
        !active.data.some((r) => r.employee_id === "E100")
        && active.total === everyone.total - 1,
        `${active.total} of ${everyone.total}`);

    // TOGETHER, not one instead of the other.
    const both = await list("?department=Design&role=employee");
    check("filters combine rather than replacing each other",
        both.data.every((r) => r.department === "Design" && r.role === "employee")
        && both.total === 2, `total=${both.total}`);

    const searched = await list("?department=Design&search=Meera");
    check("and they combine with the search box",
        searched.total === 1 && searched.data[0]?.employee_id === "C001",
        JSON.stringify(searched.data.map((r) => r.employee_id)));

    // A FILTER NOBODY ASKED FOR IS NOT A FILTER. An unknown role would
    // otherwise return nothing at all and read as "there are no admins".
    const nonsense = await list("?role=wizard");
    check("an unknown role is ignored, not treated as a match on nothing",
        nonsense.total === everyone.total, `total=${nonsense.total}`);

    // The department list the filter is built from is the company's, not
    // whatever happened to be on the page that was asked for.
    const onePage = await list("?limit=1");
    check("the departments offered are every department, not the page's",
        Array.isArray(onePage.departments)
        && onePage.departments.includes("Design")
        && onePage.departments.includes("Engineering"),
        JSON.stringify(onePage.departments));

    // ── the database refuses what the route would have let through ──────
    //
    // The CHECK constraints are the second line, for anything reaching the
    // table another way. Written straight to psql, past the controller.
    for (const [sql, label] of [
        [`UPDATE employees SET pan = 'nope' WHERE employee_id = 'E001'`,
         "a malformed PAN"],
        [`UPDATE employees SET bank_ifsc = 'nope' WHERE employee_id = 'E001'`,
         "a malformed IFSC"],
        [`UPDATE employees SET payment_mode = 'barter' WHERE employee_id = 'E001'`,
         "an unknown payment mode"],
        [`UPDATE employees SET gender = 'unspecified' WHERE employee_id = 'E001'`,
         "a gender off the list"],
    ]) {
        let refused = false;
        try { psql(DB, sql); } catch (_) { refused = true; }
        check(`the table itself refuses ${label}`, refused);
    }

    await pool.end();
    server.close();
    console.log(`\n${failures ? `${failures} FAILED` : "ALL PASS"}\n`);
    try { execFileSync("dropdb", ["--if-exists", DB]); } catch (_) {}
    process.exit(failures ? 1 : 0);
}

main().catch((error) => {
    console.error(error);
    try { execFileSync("dropdb", ["--if-exists", DB]); } catch (_) {}
    process.exit(1);
});
