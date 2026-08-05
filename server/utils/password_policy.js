/**
 * One place that decides whether a password is acceptable.
 *
 * Three endpoints set passwords — account creation, an employee changing
 * their own, and an admin issuing a reset. If each carried its own rules,
 * the weakest one would decide the real policy: an admin reset that allowed
 * "123" would undo whatever the change screen enforced.
 */

const MIN_LENGTH = 8;
const MAX_LENGTH = 128;

/**
 * Passwords that are long enough to pass the length check but are still the
 * first thing anyone would try. Not a serious dictionary — it is here to
 * catch the handful that get typed when someone is in a hurry.
 */
const OBVIOUS = new Set([
    "password", "password1", "password123", "12345678", "123456789",
    "qwertyui", "iloveyou", "admin123", "welcome1", "changeme",
    "letmein1", "abc12345", "amaze123",
]);

/**
 * @returns {string|null} null if acceptable, otherwise the reason to show
 *   the user. The message is written to be displayed as-is.
 */
function validatePassword(password, { username = "", employeeId = "" } = {}) {
    if (typeof password !== "string" || password.length === 0) {
        return "Password is required.";
    }
    if (password.length < MIN_LENGTH) {
        return `Password must be at least ${MIN_LENGTH} characters.`;
    }
    if (password.length > MAX_LENGTH) {
        return `Password must be at most ${MAX_LENGTH} characters.`;
    }
    // bcrypt silently truncates at 72 bytes, so a password that differs only
    // beyond that point would still let the old one through. Reject it here
    // rather than let the difference be ignored.
    if (Buffer.byteLength(password, "utf8") > 72) {
        return "Password is too long — use 72 characters or fewer.";
    }
    if (password.trim().length === 0) {
        return "Password cannot be only spaces.";
    }

    const lower = password.toLowerCase();
    if (OBVIOUS.has(lower)) {
        return "That password is too easy to guess — choose another.";
    }
    if (username && lower === String(username).toLowerCase()) {
        return "Password cannot be the same as the username.";
    }
    if (employeeId && lower === String(employeeId).toLowerCase()) {
        return "Password cannot be the same as the employee ID.";
    }
    return null;
}

/**
 * A temporary password for an admin-issued reset.
 *
 * Deliberately readable: an admin reads this out over a phone call or types
 * it into a chat, so it avoids characters that are easy to confuse (0/O,
 * 1/l/I). It is single-use in practice — the reset marks the account
 * `must_change_password`, so the employee replaces it at next login.
 */
function generateTemporaryPassword() {
    const alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    const { randomInt } = require("crypto");
    let out = "";
    for (let i = 0; i < 12; i += 1) {
        out += alphabet[randomInt(alphabet.length)];
    }
    return out;
}

module.exports = { validatePassword, generateTemporaryPassword, MIN_LENGTH };
