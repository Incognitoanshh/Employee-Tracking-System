/**
 * The timer's entry point, kept apart from the sending itself.
 *
 * utils/alert_mailer.js requires the alerts controller, which requires the
 * database and the rules — a chain that must not be pulled in while server.js
 * is still assembling its routes. Requiring it INSIDE the function means the
 * first tick loads it, ten minutes after boot, by which time everything
 * exists.
 *
 * It also keeps server.js honest: the file that starts the timer says what
 * the timer is for and nothing else.
 */
async function runAlertEmails() {
    const { runAlertEmails: run } = require("./alert_mailer");
    return run();
}

module.exports = { runAlertEmails };
