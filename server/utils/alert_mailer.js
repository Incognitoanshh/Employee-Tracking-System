/**
 * Alerts, by email, without becoming noise.
 *
 * THE PROBLEM THIS IS SHAPED AROUND. Alerts are derived, not stored — the
 * rules work them out fresh from attendance and activity every time anybody
 * asks. So "Rajesh has not logged in" is true at 09:40 and still true at
 * 09:45, 09:50 and every five minutes until he does or the day ends. Mail it
 * each time and the owner has eighty messages by lunch, filters the sender
 * into a folder, and never sees the one that mattered.
 *
 * So: ONE EMAIL PER PERSON PER KIND OF PROBLEM PER DAY, recorded in
 * alert_emails and keyed on the IST day. Tomorrow is a different day and
 * tomorrow's alert is sent; nothing has to expire anything.
 *
 * TWO KINDS OF MAIL, and the split is deliberate:
 *
 *   IMMEDIATE, for the things that mean somebody is not working right now and
 *   somebody else could do something about it today — an app that has stopped
 *   reporting, an account that has never reported, a shift nobody signed in
 *   for. These are worth interrupting for.
 *
 *   A DAILY SUMMARY in the evening for everything, including the things that
 *   are worth knowing and not worth interrupting for — idle time, mainly. One
 *   message with the day's list beats four messages saying somebody was idle.
 *
 * Which types are immediate is a setting, so an owner who disagrees with that
 * judgement can move a type either way without touching this file.
 *
 * FAILURES ARE KEPT. A row is written when a send is ATTEMPTED, with what
 * happened, so "I never got the alert" can be answered with evidence rather
 * than a shrug: sent, refused by the mail server, or never generated.
 * A failed row is retried on the next run, up to MAX_ATTEMPTS.
 */
const pool = require("../config/db");
const mailer = require("./mailer");
const { istToday } = require("./ist_sql");

// Enough to survive a mail server having a bad ten minutes; few enough that a
// permanently wrong password does not mean a row retried for ever.
const MAX_ATTEMPTS = 4;

const SEVERITY_COLOUR = {
    HIGH:   "#dc2626",
    MEDIUM: "#f59e0b",
    LOW:    "#2563eb",
};

/** The settings this module reads, with what they mean when unset. */
async function loadMailSettings() {
    const keys = ["alert_email_to", "alert_email_immediate",
                  "alert_email_digest", "alert_email_digest_hour"];
    const rows = (await pool.query(
        `SELECT key, value FROM app_settings WHERE key = ANY($1)`, [keys])).rows;
    const saved = Object.fromEntries(rows.map((r) => [r.key, r.value]));

    const recipients = String(saved.alert_email_to || "")
        .split(/[,;\s]+/)
        .map((address) => address.trim())
        .filter(Boolean);

    return {
        recipients,
        immediate: new Set(String(saved.alert_email_immediate
                                  ?? "NOT_REPORTING,NEVER_REPORTED,NO_LOGIN")
            .split(",").map((t) => t.trim()).filter(Boolean)),
        digest: String(saved.alert_email_digest ?? "true") === "true",
        digestHour: Number(saved.alert_email_digest_hour ?? 19),
    };
}

// ────────────────────────────────────────────────────────── the templates

const SHELL = (heading, intro, body) => `<!doctype html>
<html><body style="margin:0;padding:0;background:#f1f5f9;
     font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:#f1f5f9;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="max-width:600px;background:#ffffff;border-radius:12px;
                    border:1px solid #e2e8f0;overflow:hidden;">
        <tr><td style="padding:20px 24px;background:#0f172a;">
          <div style="color:#ffffff;font-size:16px;font-weight:700;">Amaze Connect</div>
          <div style="color:#94a3b8;font-size:12px;margin-top:2px;">${heading}</div>
        </td></tr>
        <tr><td style="padding:22px 24px;color:#0f172a;font-size:14px;line-height:1.55;">
          <p style="margin:0 0 16px 0;color:#334155;">${intro}</p>
          ${body}
        </td></tr>
        <tr><td style="padding:16px 24px;background:#f8fafc;border-top:1px solid #e2e8f0;
                       color:#64748b;font-size:11px;line-height:1.5;">
          Sent by Amaze Connect because this address is listed under
          Alerts&nbsp;→&nbsp;Email. Thresholds are set in Configuration.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>`;

/**
 * Escaped, always.
 *
 * Names and detail lines come from the database, and a name is whatever
 * somebody typed. An apostrophe would break the markup and a script tag would
 * be worse — a mail client that renders it is a mail client running somebody
 * else's script.
 */
function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function alertCard(alert) {
    const colour = SEVERITY_COLOUR[alert.severity] || "#64748b";
    return `
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="border:1px solid #e2e8f0;border-left:4px solid ${colour};
                    border-radius:8px;margin:0 0 12px 0;">
        <tr><td style="padding:14px 16px;">
          <div style="font-size:11px;font-weight:700;color:${colour};
                      letter-spacing:.4px;">${escapeHtml(alert.severity)}</div>
          <div style="font-size:15px;font-weight:700;margin:4px 0 2px 0;">
            ${escapeHtml(alert.employee_name || alert.employee_id)}</div>
          <div style="font-size:13px;font-weight:600;color:#0f172a;">
            ${escapeHtml(alert.title)}</div>
          <div style="font-size:12px;color:#475569;margin-top:6px;">
            ${escapeHtml(alert.detail)}</div>
        </td></tr>
      </table>`;
}

/** The same message as plain text, for clients that will not render HTML. */
function alertText(alert) {
    return `[${alert.severity}] ${alert.employee_name || alert.employee_id}\n`
         + `  ${alert.title}\n  ${alert.detail}\n`;
}

// ────────────────────────────────────────────────────────── sending

/**
 * Has this already gone out today, and can it be tried again?
 *
 * Returns the row if one exists. A row with status 'sent' means do nothing; a
 * failed row under MAX_ATTEMPTS means try again and count the attempt.
 */
async function existingRow(employeeId, type, istDay) {
    const rows = await pool.query(
        `SELECT id, status, attempts FROM alert_emails
          WHERE COALESCE(employee_id, '~digest~') = COALESCE($1, '~digest~')
            AND alert_type = $2 AND ist_day = $3`,
        [employeeId, type, istDay]);
    return rows.rows[0] || null;
}

async function recordAttempt({ employeeId, type, istDay, severity, subject,
                               recipients, error, previous }) {
    const status = error ? "failed" : "sent";
    if (previous) {
        await pool.query(
            `UPDATE alert_emails
                SET status = $2, attempts = attempts + 1, error = $3,
                    sent_at = NOW() AT TIME ZONE 'UTC'
              WHERE id = $1`,
            [previous.id, status, error || null]);
        return;
    }
    await pool.query(
        `INSERT INTO alert_emails
             (employee_id, alert_type, ist_day, severity, subject,
              recipients, status, attempts, error)
         VALUES ($1, $2, $3, $4, $5, $6, $7, 1, $8)
         ON CONFLICT (COALESCE(employee_id, '~digest~'), alert_type, ist_day)
         DO UPDATE SET status = EXCLUDED.status,
                       attempts = alert_emails.attempts + 1,
                       error = EXCLUDED.error,
                       sent_at = NOW() AT TIME ZONE 'UTC'`,
        [employeeId, type, istDay, severity, subject,
         recipients.join(", "), status, error || null]);
}

async function sendOne({ alert, recipients, istDay }) {
    const previous = await existingRow(alert.employee_id, alert.type, istDay);
    // ALREADY SENT TODAY. This is the whole point of the table.
    if (previous && previous.status === "sent") return "skipped";
    if (previous && previous.attempts >= MAX_ATTEMPTS) return "given-up";

    const who = alert.employee_name || alert.employee_id;
    const subject = `[${alert.severity}] ${who} — ${alert.title}`;
    try {
        await mailer.send({
            to: recipients.join(", "),
            subject,
            text: `${alertText(alert)}\nAmaze Connect`,
            html: SHELL("Alert", "This needs attention:", alertCard(alert)),
        });
        await recordAttempt({ employeeId: alert.employee_id, type: alert.type,
                              istDay, severity: alert.severity, subject,
                              recipients, previous });
        return "sent";
    } catch (error) {
        await recordAttempt({ employeeId: alert.employee_id, type: alert.type,
                              istDay, severity: alert.severity, subject,
                              recipients, error: error.message, previous });
        return "failed";
    }
}

async function sendDigest({ alerts, recipients, istDay, counts }) {
    const previous = await existingRow(null, "DIGEST", istDay);
    if (previous && previous.status === "sent") return "skipped";
    if (previous && previous.attempts >= MAX_ATTEMPTS) return "given-up";

    const subject = alerts.length
        ? `Amaze Connect — ${alerts.length} alert${alerts.length === 1 ? "" : "s"} today`
        : "Amaze Connect — nothing to report today";

    // A QUIET DAY IS WORTH SAYING. A summary that only arrives when something
    // is wrong cannot be told apart from a summary that stopped working.
    const body = alerts.length
        ? alerts.map(alertCard).join("")
        : `<p style="margin:0;color:#15803d;font-weight:600;">
             Nothing needed attention today.</p>`;
    const intro = alerts.length
        ? `${counts.HIGH} high, ${counts.MEDIUM} medium and ${counts.LOW} low `
        + `priority for ${istDay}.`
        : `No alerts were raised on ${istDay}.`;

    try {
        await mailer.send({
            to: recipients.join(", "),
            subject,
            text: (alerts.length ? alerts.map(alertText).join("\n")
                                 : "Nothing needed attention today.\n")
                + "\nAmaze Connect",
            html: SHELL(`Daily summary · ${istDay}`, intro, body),
        });
        await recordAttempt({ employeeId: null, type: "DIGEST", istDay,
                              severity: null, subject, recipients, previous });
        return "sent";
    } catch (error) {
        await recordAttempt({ employeeId: null, type: "DIGEST", istDay,
                              severity: null, subject, recipients,
                              error: error.message, previous });
        return "failed";
    }
}

/**
 * One pass. Safe to call as often as you like — the table decides what has
 * already gone out.
 *
 * @returns {Promise<{sent:number, skipped:number, failed:number, reason?:string}>}
 */
async function runAlertEmails({ now = new Date() } = {}) {
    const tally = { sent: 0, skipped: 0, failed: 0 };

    const why = mailer.unavailableReason();
    if (why) return { ...tally, reason: why };

    const config = await loadMailSettings();
    if (config.recipients.length === 0) {
        return { ...tally, reason: "No recipients — set one under Alerts → Email." };
    }

    // Loaded here rather than imported at the top: the controller requires
    // this module's siblings, and a cycle at load time would leave one of
    // them half-built.
    const { collectAlerts } = require("../controllers/alerts.controller");
    const { enabled, alerts, counts } = await collectAlerts();
    if (!enabled) return { ...tally, reason: "Alerts are switched off." };

    const istDay = (await pool.query(
        `SELECT (${istToday()})::text AS d`)).rows[0].d;

    for (const alert of alerts) {
        if (!config.immediate.has(alert.type)) continue;
        const outcome = await sendOne({ alert, recipients: config.recipients, istDay });
        if (outcome === "sent") tally.sent += 1;
        else if (outcome === "failed") tally.failed += 1;
        else tally.skipped += 1;
    }

    // The summary goes out once the day is far enough along to summarise.
    const istHour = Number((await pool.query(
        `SELECT EXTRACT(HOUR FROM (NOW() AT TIME ZONE 'Asia/Kolkata'))::int AS h`
    )).rows[0].h);
    if (config.digest && istHour >= config.digestHour) {
        const outcome = await sendDigest({ alerts, recipients: config.recipients,
                                           istDay, counts });
        if (outcome === "sent") tally.sent += 1;
        else if (outcome === "failed") tally.failed += 1;
        else tally.skipped += 1;
    }

    return tally;
}

module.exports = {
    runAlertEmails,
    loadMailSettings,
    escapeHtml,
    MAX_ATTEMPTS,
};
