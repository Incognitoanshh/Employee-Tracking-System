/**
 * The thing that brings the schema up to date, on the way up.
 *
 * WHY THIS FILE EXISTS AT ALL. utils/migrate.js was written to close a real
 * hole — deploying is `git pull && pm2 restart`, which moves code and not
 * schema, so a release carrying a migration reached production with the new
 * code running against the old tables. The symptom was a page of dashes.
 *
 * It then shipped with NO TEST OF ITS OWN, and the first time it ran on the
 * real server it produced twenty failures in a row: it was reading macOS
 * AppleDouble files ("._name.sql") as migrations, and it repeated the same
 * permission error once per file for a database user that — correctly — is
 * not allowed to change the schema at all. Both were fixed by reading the
 * output and guessing, which is exactly what a test is for.
 *
 * WHAT IS CHECKED HERE
 *   * a pending migration is applied, and recorded, and not applied twice;
 *   * an AppleDouble file is not mistaken for a migration;
 *   * a user who may not alter the schema produces ONE message naming the
 *     script that can, not one per file;
 *   * a broken migration does not stop the server from starting, and is
 *     named by /api/health afterwards.
 *
 * Run:  node server/tests/test_migrate.js
 */
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

const DB = `ets_migrate_${process.pid}`;
const ROOT = path.resolve(__dirname, "..", "..");

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}

function psql(db, sql) {
    return execFileSync("psql", ["-d", db, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        { encoding: "utf8" }).trim();
}

/**
 * A migrations directory of our own.
 *
 * The runner is pointed at it by loading it with MIGRATIONS_DIR overridden,
 * so these checks never depend on what the real directory happens to hold —
 * a test that asserts "seventeen migrations applied" breaks every time
 * somebody writes an eighteenth, and gets deleted rather than fixed.
 */
function runnerFor(dir) {
    const file = path.join(ROOT, "server", "utils", "migrate.js");
    delete require.cache[require.resolve(file)];
    const source = fs.readFileSync(file, "utf8").replace(
        'const MIGRATIONS_DIR = path.resolve(__dirname, "..", "migrations");',
        `const MIGRATIONS_DIR = ${JSON.stringify(dir)};`);
    const temp = path.join(dir, "_runner.js");
    fs.writeFileSync(temp, source);
    delete require.cache[require.resolve(temp)];
    return require(temp);
}

async function main() {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ets_migdir_"));
    const { Pool } = require(path.join(ROOT, "server", "node_modules", "pg"));
    let pool;

    console.log(`Migration runner (${DB})\n`);
    try {
        execFileSync("psql", ["-d", "postgres", "-q", "-c",
            `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`]);
        execFileSync("psql", ["-d", "postgres", "-q", "-c", `CREATE DATABASE ${DB}`]);

        pool = new Pool({
            host: process.env.PGHOST || "127.0.0.1",
            port: Number(process.env.PGPORT || 5432),
            database: DB,
            user: process.env.PGUSER || process.env.USER,
            password: process.env.PGPASSWORD || undefined,
        });

        fs.writeFileSync(path.join(dir, "2026_01_01_one.sql"),
            `CREATE TABLE IF NOT EXISTS mig_one (id int);`);
        fs.writeFileSync(path.join(dir, "2026_01_02_two.sql"),
            `CREATE TABLE IF NOT EXISTS mig_two (id int);`);

        // THE APPLEDOUBLE. macOS writes one of these beside any file copied
        // from a Mac, and on the server they sat in migrations/ looking
        // exactly like migrations. They are binary, so Postgres answered
        // "invalid message format" — five failures that said nothing about
        // the schema and sent somebody looking for a database problem.
        fs.writeFileSync(path.join(dir, "._2026_01_01_one.sql"),
            Buffer.from([0x00, 0x05, 0x16, 0x07, 0x00, 0x02, 0x00, 0x00]));

        console.log("A directory with two migrations and one piece of macOS litter");
        let result = await runnerFor(dir).applyPendingMigrations(pool);
        check("both real migrations are applied",
            result.applied.length === 2, JSON.stringify(result.applied));
        check("the AppleDouble is not one of them",
            !result.applied.some((n) => n.startsWith(".")),
            JSON.stringify(result.applied));
        check("and it is not reported as a failure either",
            result.failed.length === 0, JSON.stringify(result.failed));
        check("the tables exist",
            psql(DB, `SELECT count(*) FROM information_schema.tables
                       WHERE table_name IN ('mig_one','mig_two')`) === "2");
        check("and each is written down",
            psql(DB, `SELECT count(*) FROM schema_migrations`) === "2");

        console.log("\nRunning again changes nothing");
        result = await runnerFor(dir).applyPendingMigrations(pool);
        check("nothing is applied a second time",
            result.applied.length === 0, JSON.stringify(result.applied));
        check("and the record is unchanged",
            psql(DB, `SELECT count(*) FROM schema_migrations`) === "2");

        console.log("\nA migration that is simply wrong");
        // The server MUST still come up. A monitoring product that refuses to
        // start leaves every client unable to report anything, which is worse
        // than serving what still works.
        fs.writeFileSync(path.join(dir, "2026_01_03_broken.sql"),
            `ALTER TABLE table_that_does_not_exist ADD COLUMN x int;`);
        const runner = runnerFor(dir);
        result = await runner.applyPendingMigrations(pool);
        check("it is reported as failed", result.failed.length === 1,
            JSON.stringify(result.failed.map((f) => f.name)));
        check("and health names it, so a deployment can be checked with a curl",
            runner.migrationStatus().up_to_date === false
            && runner.migrationStatus().failed.includes("2026_01_03_broken.sql"),
            JSON.stringify(runner.migrationStatus()));
        check("a failed migration is NOT recorded as applied",
            psql(DB, `SELECT count(*) FROM schema_migrations`) === "2");
        fs.rmSync(path.join(dir, "2026_01_03_broken.sql"));

        console.log("\nA database user who may not change the schema");
        // NOT A FAULT. The application's user deliberately does not own the
        // tables — it reads and writes rows and cannot alter the schema,
        // which is the right way round for a service facing the internet.
        // On such a deployment EVERY migration answers "must be owner of
        // table …", and printing that once per file buries the one line
        // somebody needs. This is what the real server did: twenty of them.
        psql(DB, `DROP ROLE IF EXISTS ets_reader_${process.pid}`);
        psql(DB, `CREATE ROLE ets_reader_${process.pid} LOGIN PASSWORD 'x'`);
        psql(DB, `GRANT CONNECT ON DATABASE ${DB} TO ets_reader_${process.pid}`);
        // CREATE as well as USAGE, and this matters: the production user CAN
        // create a table — schema_migrations was created by the runner there
        // on its first boot. What it cannot do is ALTER a table owned by
        // somebody else, which is every table in the schema. Without the
        // CREATE grant this test models a different, easier failure and never
        // reaches the one that actually happened.
        psql(DB, `GRANT USAGE, CREATE ON SCHEMA public TO ets_reader_${process.pid}`);
        psql(DB, `GRANT SELECT, INSERT ON schema_migrations TO ets_reader_${process.pid}`);

        for (const n of [4, 5, 6]) {
            fs.writeFileSync(path.join(dir, `2026_01_0${n}_more.sql`),
                `ALTER TABLE mig_one ADD COLUMN IF NOT EXISTS c${n} int;`);
        }

        const limited = new Pool({
            host: process.env.PGHOST || "127.0.0.1",
            port: Number(process.env.PGPORT || 5432),
            database: DB,
            user: `ets_reader_${process.pid}`,
            password: "x",
        });
        const said = [];
        const realError = console.error;
        console.error = (...args) => said.push(args.join(" "));
        const limitedRunner = runnerFor(dir);
        try {
            result = await limitedRunner.applyPendingMigrations(limited);
        } finally {
            console.error = realError;
            await limited.end();
        }

        check("it gives up at the first one rather than trying all three",
            result.failed.length === 1,
            `${result.failed.length} attempted — the server printed twenty of these`);
        check("it says so exactly once", said.length === 1, String(said.length));
        check("and names the script that CAN do it",
            said.join(" ").includes("server/scripts/migrate.sh"),
            said.join(" ").slice(0, 160));
        check("health says the schema is behind, not that a migration failed",
            limitedRunner.migrationStatus().up_to_date === false
            && /migrate\.sh/.test(limitedRunner.migrationStatus().message || ""),
            JSON.stringify(limitedRunner.migrationStatus()));
    } finally {
        if (pool) await pool.end().catch(() => {});
        try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
        try { psql("postgres", `DROP ROLE IF EXISTS ets_reader_${process.pid}`); } catch (_) {}
        try { fs.rmSync(dir, { recursive: true, force: true }); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.stdout.write("", () => process.exit(1));
    } else {
        console.log("all migration runner checks passed");
        process.stdout.write("", () => process.exit(0));
    }
}

main().catch((error) => {
    console.error(error);
    try { psql("postgres", `DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`); } catch (_) {}
    try { psql("postgres", `DROP ROLE IF EXISTS ets_reader_${process.pid}`); } catch (_) {}
    process.exit(1);
});
