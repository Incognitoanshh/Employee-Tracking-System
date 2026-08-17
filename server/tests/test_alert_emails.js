/**
 * Alerts by email, and the one thing that decides whether they are read.
 *
 * Alerts are DERIVED, not stored: the rules work them out fresh every time
 * anybody asks. So "Rajesh has not logged in" is true at 09:40 and still true
 * at 09:45, 09:50 and every ten minutes until he does. Mail it each time and
 * the owner has eighty messages by lunch, filters the sender into a folder,
 * and never sees the one that mattered — which is worse than never having
 * sent any, because the failure is invisible.
 *
 * So the thing this file spends most of its length on is NOT that an email
 * goes out. It is that the SECOND one does not.
 *
 * Nothing is actually sent: mailer.send is replaced, which is also how the
 * message itself is inspected — the subject, the escaping, and the fact that
 * a name somebody typed cannot become markup in a mail client.
 *
 * Run:  node server/tests/test_alert_emails.js
 */
const { execFileSync } = require("child_process");
const path = require("path");
const { migrate } = require("./_migrate");

const DB = `ets_alertmail_${process.pid}`;
const ROOT = path.resolve(__dirname, "..", "..");

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}

function psql(sql, db = DB) {
    return execFileSync("psql", ["-d", db, "-q", "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        { encoding: "utf8" }).trim();
}

async function main() {
    console.log(`Alert emails (${DB})\n`);
    try {
        migrate(DB);

        Object.assign(process.env, {
            DB_HOST: process.env.PGHOST || "127.0.0.1",
            DB_PORT: process.env.PGPORT || "5432",
            DB_NAME: DB,
            DB_USER: process.env.PGUSER || process.env.USER,
            DB_PASSWORD: process.env.PGPASSWORD || "unused-locally",
            JWT_SECRET: "test-secret-not-used-in-production",
            ENCRYPTION_KEY: "0".repeat(64),
            // Enough for the mailer to consider itself configured. Nothing is
            // sent — `send` is replaced below.
            SMTP_HOST: "smtp.invalid",
            SMTP_USER: "alerts@amaze.test",
            SMTP_PASS: "not-a-real-password",
        });

        const mailer = require(path.join(ROOT, "server", "utils", "mailer.js"));
        const posted = [];
        let refuse = null;
        mailer.send = async (message) => {
            if (refuse) throw new Error(refuse);
            posted.push(message);
        };

        const alertMailer = require(path.join(ROOT, "server", "utils", "alert_mailer.js"));

        // Somebody whose shift began hours ago and who has not signed in —
        // the alert this feature exists for.
        psql(`INSERT INTO employees (employee_id, username, password, role, full_name)
              VALUES ('E001','rajesh','x','employee','Rajesh Kumar')`);
        psql(`INSERT INTO employee_configs (employee_id, shift_start, shift_end,
                                            late_grace_minutes)
              VALUES ('E001','00:01','23:59', 0)`);
        psql(`INSERT INTO app_settings (key, value) VALUES ('alert_email_to','')
              ON CONFLICT (key) DO UPDATE SET value = ''`);

        console.log("With nobody to send to");
        let result = await alertMailer.runAlertEmails();
        check("nothing is sent", posted.length === 0 && result.sent === 0);
        check("and it says why, rather than looking like it worked",
            /recipient/i.test(result.reason || ""), String(result.reason));

        psql(`UPDATE app_settings SET value = 'owner@amaze.test' WHERE key = 'alert_email_to'`);
        // Only the immediate kind, so the daily summary does not confuse the
        // counting below; it has its own section.
        psql(`UPDATE app_settings SET value = 'false' WHERE key = 'alert_email_digest'`);

        console.log("\nThe first time an alert is true");
        result = await alertMailer.runAlertEmails();
        check("an email goes out", result.sent === 1, JSON.stringify(result));
        check("to the address that was set",
            posted[0] && posted[0].to === "owner@amaze.test", JSON.stringify(posted[0]?.to));
        // An account that has never sent anything raises NEVER_REPORTED
        // rather than NO_LOGIN — it is a different problem with a different
        // answer (the app is probably not installed), and the subject has to
        // say which, because that is all somebody sees in a notification.
        check("the subject names the person, the severity and the problem",
            /Rajesh Kumar/.test(posted[0].subject)
            && /^\[(HIGH|MEDIUM|LOW)\]/.test(posted[0].subject)
            && /reported|logged in|idle/i.test(posted[0].subject),
            posted[0].subject);
        check("it carries a readable text part as well as HTML",
            Boolean(posted[0].text) && Boolean(posted[0].html),
            `text=${Boolean(posted[0]?.text)} html=${Boolean(posted[0]?.html)}`);
        check("and the delivery is written down",
            psql(`SELECT status FROM alert_emails WHERE employee_id='E001'`) === "sent");

        console.log("\nAND THE SECOND TIME IT IS STILL TRUE — the whole point");
        // This is what makes the difference between a useful alert and a
        // filter rule. Ten more runs; the alert is true throughout.
        for (let i = 0; i < 10; i += 1) await alertMailer.runAlertEmails();
        check("no second email is sent, however many times it runs",
            posted.length === 1, `${posted.length} emails for one alert`);
        check("and only one row exists for it",
            psql(`SELECT COUNT(*) FROM alert_emails WHERE employee_id='E001'`) === "1");

        console.log("\nTomorrow is a different day");
        psql(`UPDATE alert_emails SET ist_day = ist_day - 1`);
        result = await alertMailer.runAlertEmails();
        check("the alert is sent again", posted.length === 2, `${posted.length}`);
        check("as its own row", psql(`SELECT COUNT(*) FROM alert_emails`) === "2");

        console.log("\nWhen the mail server refuses");
        psql(`DELETE FROM alert_emails`);
        posted.length = 0;
        refuse = "550 mailbox unavailable";
        result = await alertMailer.runAlertEmails();
        check("the failure is counted, not thrown", result.failed === 1,
            JSON.stringify(result));
        check("the row says it failed",
            psql(`SELECT status FROM alert_emails WHERE employee_id='E001'`) === "failed");
        check("and keeps what the server said, so it can be acted on",
            psql(`SELECT error FROM alert_emails WHERE employee_id='E001'`)
                .includes("550"),
            psql(`SELECT error FROM alert_emails WHERE employee_id='E001'`));

        console.log("\nA failure is retried, but not for ever");
        for (let i = 0; i < 3; i += 1) await alertMailer.runAlertEmails();
        const attempts = Number(psql(
            `SELECT attempts FROM alert_emails WHERE employee_id='E001'`));
        check("the attempts are counted", attempts >= 2, String(attempts));
        check("and it stops at the limit",
            attempts <= alertMailer.MAX_ATTEMPTS, `${attempts} > ${alertMailer.MAX_ATTEMPTS}`);

        refuse = null;
        psql(`DELETE FROM alert_emails`);
        posted.length = 0;
        await alertMailer.runAlertEmails();
        check("once the mail server recovers, it sends again", posted.length === 1);

        console.log("\nA name somebody typed cannot become markup");
        // The name goes into an HTML email that somebody's mail client
        // renders. A client that runs what is in it is running whatever was
        // typed into the employee form.
        psql(`UPDATE employees SET full_name = '<script>alert(1)</script>'
               WHERE employee_id='E001'`);
        psql(`DELETE FROM alert_emails`);
        posted.length = 0;
        await alertMailer.runAlertEmails();
        check("the script tag is escaped in the HTML",
            posted[0] && !posted[0].html.includes("<script>")
            && posted[0].html.includes("&lt;script&gt;"),
            (posted[0]?.html || "").slice(0, 200));
        psql(`UPDATE employees SET full_name = 'Rajesh Kumar' WHERE employee_id='E001'`);

        console.log("\nThe daily summary");
        psql(`UPDATE app_settings SET value = 'true'  WHERE key = 'alert_email_digest'`);
        psql(`UPDATE app_settings SET value = '0'     WHERE key = 'alert_email_digest_hour'`);
        psql(`DELETE FROM alert_emails`);
        posted.length = 0;
        await alertMailer.runAlertEmails();
        const digest = posted.find((m) => /summary|alert/i.test(m.subject)
                                       && !/^\[/.test(m.subject));
        check("a summary goes out once the hour has come", Boolean(digest),
            JSON.stringify(posted.map((m) => m.subject)));
        check("and only one a day, however often it runs",
            (await (async () => {
                const before = posted.length;
                for (let i = 0; i < 5; i += 1) await alertMailer.runAlertEmails();
                return posted.length === before;
            })()),
            "the summary was sent more than once");

        console.log("\nAlerts switched off means no email at all");
        psql(`INSERT INTO app_settings (key, value) VALUES ('alerts_enabled','false')
              ON CONFLICT (key) DO UPDATE SET value = 'false'`);
        psql(`DELETE FROM alert_emails`);
        posted.length = 0;
        result = await alertMailer.runAlertEmails();
        check("nothing is sent", posted.length === 0, `${posted.length}`);
        check("and it says so", /switched off/i.test(result.reason || ""),
            String(result.reason));
    } finally {
        try { psql(`DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`, "postgres"); } catch (_) {}
    }

    console.log();
    if (failures) {
        console.log(`${failures} failure(s)`);
        process.stdout.write("", () => process.exit(1));
    } else {
        console.log("all alert email checks passed");
        process.stdout.write("", () => process.exit(0));
    }
}

main().catch((error) => {
    console.error(error);
    try { psql(`DROP DATABASE IF EXISTS ${DB} WITH (FORCE)`, "postgres"); } catch (_) {}
    process.exit(1);
});
