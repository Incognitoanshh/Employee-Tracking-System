/**
 * Build a test database the way production is built: schema, then every
 * migration, in order.
 *
 * Each suite used to list the migrations it thought it needed. That list was
 * hand-written, per suite, and there are eleven of them — so adding a
 * migration meant remembering eleven places, and forgetting one produced a
 * database that was half-new and failed somewhere unrelated. Adding
 * `device_id` to active_sessions broke ten suites at once in exactly that
 * way, with "login returns 500" as the only symptom.
 *
 * It is the same fragility that took production down the same evening: a
 * migration that did not apply, and nothing anywhere that knew it had not.
 *
 * So nobody lists anything. Everything in migrations/ runs, sorted, which is
 * also the order a real deployment applies them in.
 */
const { execFileSync, execSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");

/**
 * Create `db` and bring it fully up to date.
 *
 * @param {string} db          database name, created fresh
 * @param {object} [options]
 * @param {string[]} [options.skip] migration basenames to leave out, for a
 *   suite that is deliberately testing an older shape.
 */
function migrate(db, { skip = [] } = {}) {
    execSync(`psql -d postgres -q -c "DROP DATABASE IF EXISTS ${db} WITH (FORCE)"`,
        { stdio: "pipe" });
    execSync(`psql -d postgres -q -c "CREATE DATABASE ${db}"`, { stdio: "pipe" });

    const files = [path.join(ROOT, "ets.sql")];
    const dir = path.join(ROOT, "server", "migrations");
    // Sorted by name, which is why they are dated — and why two migrations
    // written on the same day carry a sequence number. Sorting alone is not
    // enough without it: `2026_08_07_chat_phase2` came before
    // `2026_08_07_teams_chat` alphabetically and failed on a table the other
    // one had not created yet.
    //
    // The two oldest were called `add_admin_config` and `add_verbose_logging`
    // and sorted to the END, where `add_admin_config` re-added the
    // `screenshot_count` column that 2026_08_04 renames away. They were given
    // dates for that reason.
    //
    // `retention_purge.sql` is a scheduled job rather than a schema change
    // and is left out.
    const migrations = fs.readdirSync(dir)
        .filter((f) => f.endsWith(".sql") && f !== "retention_purge.sql")
        .filter((f) => !skip.includes(f))
        .sort();
    for (const name of migrations) files.push(path.join(dir, name));

    for (const file of files) {
        try {
            execFileSync("psql", ["-d", db, "-v", "ON_ERROR_STOP=1", "-q", "-f", file],
                { stdio: "pipe" });
        } catch (error) {
            // Loud, and naming the file. A migration that fails silently is
            // the whole reason this module exists.
            throw new Error(
                `migration failed: ${path.basename(file)}\n` +
                String(error.stderr || error.message).trim());
        }
    }
    return db;
}

module.exports = { migrate };
