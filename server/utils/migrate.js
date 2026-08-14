/**
 * Bringing the database up to date, on the way up.
 *
 * WHY THIS EXISTS. Deploying was `git pull && pm2 restart`. That moves the
 * code and nothing else — so a release carrying a migration reached
 * production with the new code running against the old schema. The symptom is
 * never "the migration did not run": it is a page of dashes and a 500 in a
 * log nobody is reading, on the one endpoint that happens to touch a new
 * column, while everything else looks fine.
 *
 * It happened the day My Profile shipped. Work Summary loaded, because it
 * reads tables that already existed; the profile itself and the device list
 * were 500s, because `phone` and `ip` were not there. The comment at the top
 * of tests/_migrate.js says the same thing about an earlier evening: "a
 * migration that did not apply, and nothing anywhere that knew it had not."
 *
 * Now the server applies them itself, in the same sorted order the test
 * builder uses, and records what it applied. The migrations are written to be
 * idempotent — every one uses IF NOT EXISTS or a guard — so the record is for
 * visibility rather than safety.
 *
 * IF ONE FAILS the server still comes up. A monitoring product that refuses
 * to start leaves every client unable to report anything, which is worse than
 * serving what still works; the failure is logged unmissably and shown by
 * /api/health, which is the command already used to check a deployment.
 */
const fs = require("fs");
const path = require("path");

const MIGRATIONS_DIR = path.resolve(__dirname, "..", "migrations");

// A scheduled job, not a schema change — the same exclusion tests/_migrate.js
// makes.
const NOT_A_MIGRATION = new Set(["retention_purge.sql"]);

/** What the last run found, for /api/health to report. */
let lastResult = { applied: [], pending: [], failed: [], ran: false };

function migrationFiles() {
    if (!fs.existsSync(MIGRATIONS_DIR)) return [];
    return fs.readdirSync(MIGRATIONS_DIR)
        .filter((name) => name.endsWith(".sql") && !NOT_A_MIGRATION.has(name))
        // Sorted by name, which is why they are dated and why two on the same
        // day carry a sequence number.
        .sort();
}

async function applyPendingMigrations(pool) {
    const applied = [];
    const failed = [];

    try {
        await pool.query(`
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name        TEXT PRIMARY KEY,
                applied_at  TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
            )`);
    } catch (error) {
        console.error("[MIGRATE] cannot record migrations:", error.message);
        lastResult = { applied: [], pending: migrationFiles(), failed: [], ran: false };
        return lastResult;
    }

    const done = new Set(
        (await pool.query(`SELECT name FROM schema_migrations`)).rows.map((r) => r.name));

    const files = migrationFiles();
    for (const name of files) {
        if (done.has(name)) continue;
        const sql = fs.readFileSync(path.join(MIGRATIONS_DIR, name), "utf8");
        const client = await pool.connect();
        try {
            // One transaction each: a migration that fails half-way must not
            // leave the schema in a shape nothing was written for.
            await client.query("BEGIN");
            await client.query(sql);
            await client.query(
                `INSERT INTO schema_migrations (name) VALUES ($1)
                 ON CONFLICT (name) DO NOTHING`, [name]);
            await client.query("COMMIT");
            applied.push(name);
            console.log(`[MIGRATE] applied ${name}`);
        } catch (error) {
            await client.query("ROLLBACK").catch(() => {});
            failed.push({ name, message: error.message });
            console.error(
                `[MIGRATE] FAILED ${name}: ${error.message}\n` +
                `[MIGRATE] The server is starting anyway. Anything reading the ` +
                `columns this migration adds will answer 500 until it is fixed.`);
        } finally {
            client.release();
        }
    }

    // Anything already recorded counts as done; what is left is what failed.
    lastResult = {
        applied,
        pending: failed.map((f) => f.name),
        failed,
        ran: true,
    };
    if (applied.length === 0 && failed.length === 0) {
        console.log(`[MIGRATE] schema is up to date (${files.length} migrations)`);
    }
    return lastResult;
}

/** For /api/health, so a deployment can be checked with the same curl. */
function migrationStatus() {
    return {
        up_to_date: lastResult.ran && lastResult.failed.length === 0,
        applied_now: lastResult.applied.length,
        failed: lastResult.failed.map((f) => f.name),
    };
}

module.exports = { applyPendingMigrations, migrationStatus };
