/**
 * Sending mail, when there is somewhere to send it from.
 *
 * The only thing this is used for today is the six-digit code that proves an
 * email address belongs to the person who typed it.
 *
 * IT IS OPTIONAL, AND THAT IS THE POINT. Two things can be missing on a
 * server that is otherwise perfectly healthy:
 *
 *   * the `nodemailer` package, because deploying is `git pull && pm2
 *     restart` and that does not run `npm install`. This is not a theory —
 *     the same gap sent a release to production with the code expecting
 *     columns the database did not have, and the only symptom was a page of
 *     dashes. So the require is guarded, and a server missing the package
 *     starts and serves everything else.
 *
 *   * the SMTP settings, because they are somebody's mailbox credentials and
 *     belong in .env on the machine, never in this repository.
 *
 * In both cases the answer to "can this server send mail" is a plain no, the
 * verification endpoint says so in words a person can act on, and nothing
 * else in the product changes. What must NEVER happen is a 500 — that tells
 * the client the server is broken and tells a retry loop to try again.
 *
 * WHAT GOES IN .env
 *
 *     SMTP_HOST=smtp.gmail.com
 *     SMTP_PORT=587
 *     SMTP_USER=no-reply@yourcompany.com
 *     SMTP_PASS=an app password, not the mailbox password
 *     SMTP_FROM=Amaze Connect <no-reply@yourcompany.com>     # optional
 *
 * SMTP_PASS SHOULD BE AN APP PASSWORD. A mailbox password in a .env file is
 * the whole mailbox — mail, contacts, and every account that can be reset
 * through it. An app password can be revoked on its own and does nothing else.
 */

// Guarded, for the reason in the header. `let` rather than `const` so the
// failure is a value rather than a crash at import time.
let nodemailer = null;
try {
    nodemailer = require("nodemailer");
} catch (_) {
    nodemailer = null;
}

const HOST = process.env.SMTP_HOST || "";
const PORT = Number(process.env.SMTP_PORT || 587);
const USER = process.env.SMTP_USER || "";
const PASS = process.env.SMTP_PASS || "";
const FROM = process.env.SMTP_FROM || USER;

let transport = null;

/**
 * Why mail cannot be sent, or null when it can.
 *
 * Returns a SENTENCE, because it is shown to whoever pressed the button. "Not
 * configured" on its own sends somebody looking through the code.
 */
function unavailableReason() {
    if (!nodemailer) {
        return "The mail library is not installed on the server. Run "
             + "`npm install` in server/ and restart it.";
    }
    if (!HOST || !USER || !PASS) {
        return "Email sending is not set up on this server. Add SMTP_HOST, "
             + "SMTP_USER and SMTP_PASS to server/.env and restart it.";
    }
    return null;
}

function isConfigured() {
    return unavailableReason() === null;
}

/**
 * Send one message. Resolves on success, throws with a readable message.
 *
 * The transport is built once and reused: a connection per message is slow,
 * and several providers treat a burst of new connections as abuse.
 */
async function send({ to, subject, text }) {
    const why = unavailableReason();
    if (why) throw new Error(why);

    if (transport === null) {
        transport = nodemailer.createTransport({
            host: HOST,
            port: PORT,
            // 465 is implicit TLS; 587 upgrades with STARTTLS. Getting this
            // backwards produces a timeout rather than an error that says so.
            secure: PORT === 465,
            auth: { user: USER, pass: PASS },
        });
    }

    await transport.sendMail({ from: FROM, to, subject, text });
}

module.exports = { send, isConfigured, unavailableReason };
