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
        // NOT the dot files. macOS leaves an AppleDouble "._name.sql" beside
        // anything copied from a Mac, and on the server they sat in this
        // directory looking exactly like migrations. They are binary, so the
        // first attempt to run one answered "invalid message format" — five
        // failures that had nothing to do with the schema.
        .filter((name) => !name.startsWith("."))
        .filter((name) => name.endsWith(".sql") && !NOT_A_MIGRATION.has(name))
        // Sorted by name, which is why they are dated and why two on the same
        // day carry a sequence number.
        .sort();
}

async function applyPendingMigrations(pool) {
    const applied = [];
    const failed = [];
    let notPermitted = false;

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
            // WAIT FOR A LOCK, BUT NOT FOR EVER.
            //
            // Most of these are ALTER TABLE, which needs ACCESS EXCLUSIVE —
            // it queues behind every open transaction on that table and
            // blocks every new one behind itself. This runs at boot while the
            // server is already answering requests, so without a bound a
            // single long query turns a restart into an outage, and nothing
            // in the log says why.
            //
            // Ten seconds, then give up and say so. A migration that cannot
            // get its lock is a migration to run from
            // server/scripts/migrate.sh at a quiet moment, not one to hold
            // the database open waiting for.
            await client.query("SET LOCAL lock_timeout = '10s'");
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

            // NOT ALLOWED IS NOT THE SAME AS BROKEN.
            //
            // The application's database user deliberately does not own the
            // tables — it can read and write rows and cannot alter the
            // schema, which is the right way round for a service that faces
            // the internet. On a deployment set up that way every migration
            // answers "must be owner of table …", and printing that twenty
            // times buries the one line somebody needs.
            //
            // So say it once, name the script that CAN do it, and stop.
            if (/must be owner|permission denied|insufficient privilege/i
                    .test(error.message)) {
                console.error(
                    `[MIGRATE] This database user is not allowed to change the ` +
                    `schema — which is correct, and means migrations are not ` +
                    `this process's job.\n` +
                    `[MIGRATE] ${files.length - applied.length} pending. Run:  ` +
                    `bash server/scripts/migrate.sh`);
                // NOT released here — the `finally` below does it, and doing
                // both throws "Release called on client which has already
                // been released to the pool". That exception escapes
                // applyPendingMigrations, and server.js awaits this call
                // during boot: the one code path that exists for a correctly
                // locked-down database could have taken the server down on
                // the way up.
                notPermitted = true;
                break;
            }

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
        notPermitted,
    };
    if (applied.length === 0 && failed.length === 0) {
        console.log(`[MIGRATE] schema is up to date (${files.length} migrations)`);
    }
    return lastResult;
}

/** For /api/health, so a deployment can be checked with the same curl. */
function migrationStatus() {
    if (lastResult.notPermitted) {
        return {
            up_to_date: false,
            applied_now: lastResult.applied.length,
            failed: [],
            message: "schema behind — run: bash server/scripts/migrate.sh",
        };
    }
    return {
        up_to_date: lastResult.ran && lastResult.failed.length === 0,
        applied_now: lastResult.applied.length,
        failed: lastResult.failed.map((f) => f.name),
    };
}

module.exports = { applyPendingMigrations, migrationStatus };
