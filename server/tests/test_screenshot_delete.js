/**
 * Deleting specific screenshots.
 *
 * Before this, the only way to remove a capture was to delete the whole
 * employee. A screenshot that caught a bank page, a personal message or
 * somebody else's screen could not be removed at all.
 *
 * Three things matter more than the delete itself:
 *
 *   * the FILE goes, not just the row. Deleting rows and leaving encrypted
 *     files on disk is how the last orphan pile started, and it means the
 *     thing the person wanted removed is still sitting there.
 *   * a bulk delete cannot become a way around the role hierarchy — one id
 *     belonging to an admin must refuse the whole request, not quietly skip.
 *   * the deletion is recorded. Removing evidence is exactly the action that
 *     has to leave a trace of who removed it.
 *
 * There is deliberately no equivalent for activity logs.
 *
 * Run:  node server/tests/test_screenshot_delete.js
 */
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const DB = `ets_ssdel_${process.pid}`;
const PORT = 8000 + ((process.pid + 877) % 1000);
const BASE = `http://127.0.0.1:${PORT}/api`;
const UPLOADS = path.join("/tmp", `ets-ssdel-${process.pid}`);

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
    console.log(`Deleting screenshots (${DB})\n`);

    psql("postgres", `CREATE DATABASE ${DB}`);
    fs.mkdirSync(UPLOADS, { recursive: true });
    try {
        for (const file of [
            path.join(root, "ets.sql"),
            path.join(root, "server", "migrations", "2026_08_05_password_management.sql"),
            path.join(root, "server", "migrations", "2026_08_05_username_case_insensitive.sql"),
            path.join(root, "server", "migrations", "2026_08_06_single_session.sql"),
        ]) {
            execFileSync("psql", ["-d", DB, "-v", "ON_ERROR_STOP=1", "-q", "-f", file],
                { stdio: "pipe" });
        }

        const bcrypt = require(path.join(root, "server", "node_modules", "bcryptjs"));
        const hash = await bcrypt.hash("SuperSecret123", 10);
        psql(DB, `INSERT INTO employees (employee_id, username, password, role) VALUES
            ('SA001','superadmin','${hash}','super_admin'),
            ('A001','admin1','${hash}','admin'),
            ('A002','admin2','${hash}','admin'),
            ('E001','emp1','${hash}','employee')`);

        // Files on disk alongside the rows, so "the file goes too" is a real
        // check rather than a hopeful one.
        for (const [owner, count] of [["E001", 4], ["A002", 2]]) {
            for (let i = 1; i <= count; i += 1) {
                const name = `${owner}-${i}.enc`;
                fs.writeFileSync(path.join(UPLOADS, name), "encrypted");
                psql(DB, `INSERT INTO screenshots (employee_id, file_name)
                          VALUES ('${owner}', '${name}')`);
            }
        }

        process.env.DB_HOST = process.env.PGHOST || "127.0.0.1";
        process.env.DB_PORT = process.env.PGPORT || "5432";
        process.env.DB_NAME = DB;
        process.env.DB_USER = process.env.PGUSER || process.env.USER;
        process.env.DB_PASSWORD = process.env.PGPASSWORD || "unused-locally";
        process.env.JWT_SECRET = "test-secret-not-used-in-production";
        process.env.PORT = String(PORT);
        process.env.ENCRYPTION_KEY = "0".repeat(64);
        process.env.UPLOAD_DIR = UPLOADS;

        const { server, pool } = require(path.join(root, "server", "server.js"));
        await new Promise((r) => (server.listening ? r() : server.once("listening", r)));

        const ids = (owner) => psql(DB,
            `SELECT string_agg(id::text, ',') FROM screenshots WHERE employee_id = '${owner}'`)
            .split(",").filter(Boolean).map(Number);

        let res = await api("POST", "/auth/login",
            { body: { username: "superadmin", password: "SuperSecret123" } });
        const sa = res.body.token;
        res = await api("POST", "/auth/login",
            { body: { username: "admin1", password: "SuperSecret123" } });
        const admin = res.body.token;

        // ── the delete itself ───────────────────────────────────────────
        const empIds = ids("E001");
        check("four captures to work with", empIds.length === 4, String(empIds.length));

        res = await api("POST", "/admin/screenshots/delete",
            { token: admin, body: { ids: empIds.slice(0, 2) } });
        check("an admin deletes an employee's captures",
            res.status === 200 && res.body.deleted === 2,
            `status ${res.status} ${JSON.stringify(res.body)}`);

        check("the rows are gone", ids("E001").length === 2, String(ids("E001").length));
        check("and the encrypted files with them",
            !fs.existsSync(path.join(UPLOADS, "E001-1.enc"))
            && !fs.existsSync(path.join(UPLOADS, "E001-2.enc")));
        check("the ones not selected are untouched",
            fs.existsSync(path.join(UPLOADS, "E001-3.enc"))
            && fs.existsSync(path.join(UPLOADS, "E001-4.enc")));
        check("the server reports how many files it removed",
            res.body.files_removed === 2, String(res.body.files_removed));

        // ── the hierarchy holds, including in bulk ──────────────────────
        const otherAdmin = ids("A002");
        res = await api("POST", "/admin/screenshots/delete",
            { token: admin, body: { ids: otherAdmin } });
        check("an admin cannot delete another admin's captures",
            res.status === 403, `status ${res.status}`);
        check("and nothing was deleted", ids("A002").length === 2);

        // A mixed batch must fail whole, not partially.
        res = await api("POST", "/admin/screenshots/delete",
            { token: admin, body: { ids: [...ids("E001"), ...otherAdmin] } });
        check("a mixed batch is refused rather than partly applied",
            res.status === 403, `status ${res.status}`);
        check("the employee's captures survive the refused batch",
            ids("E001").length === 2, String(ids("E001").length));

        res = await api("POST", "/admin/screenshots/delete",
            { token: sa, body: { ids: otherAdmin } });
        check("a super admin can delete an admin's captures",
            res.status === 200, `status ${res.status}`);

        // ── an employee cannot ──────────────────────────────────────────
        res = await api("POST", "/auth/login",
            { body: { username: "emp1", password: "SuperSecret123" } });
        res = await api("POST", "/admin/screenshots/delete",
            { token: res.body.token, body: { ids: ids("E001") } });
        check("an employee cannot delete screenshots at all",
            res.status === 403, `status ${res.status}`);

        // ── bad input ───────────────────────────────────────────────────
        for (const [body, label] of [
            [{ ids: [] }, "an empty list"],
            [{ ids: "5" }, "a string instead of a list"],
            [{ ids: ["abc"] }, "a non-numeric id"],
            [{}, "no ids at all"],
        ]) {
            res = await api("POST", "/admin/screenshots/delete", { token: sa, body });
            check(`${label} is refused`, res.status === 400, `status ${res.status}`);
        }

        res = await api("POST", "/admin/screenshots/delete",
            { token: sa, body: { ids: [999999] } });
        check("ids that match nothing give 404", res.status === 404, `status ${res.status}`);

        // ── it is on the record ─────────────────────────────────────────
        const logged = psql(DB,
            `SELECT COUNT(*) FROM activity_logs WHERE activity LIKE 'SCREENSHOTS DELETED%'`);
        check("every deletion is written to the audit log",
            Number(logged) >= 2, logged);

        // ── activity logs remain undeletable ────────────────────────────
        const logRoutes = fs.readFileSync(
            path.join(root, "server", "routes", "admin.routes.js"), "utf8");
        check("there is still no endpoint for deleting activity logs",
            !/logs\/delete|delete.*activity_?log/i.test(logRoutes));

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
    console.log("all screenshot delete checks passed");
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    try { fs.rmSync(UPLOADS, { recursive: true, force: true }); } catch (_) {}
    process.exit(1);
});
