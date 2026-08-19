/**
 * Turning a query string into numbers a query can be trusted with.
 *
 * WHY THIS EXISTS. Four endpoints each parsed `?page=` their own way and one
 * of them did it properly. `req.query.page` is a STRING, always -- so
 * `(page - 1) * limit` on "-1" is a NEGATIVE OFFSET, which Postgres rejects,
 * and the endpoint answers 500. Verified against the running server:
 * /admin/screenshots?page=-1 and /admin/logs?page=0 both did exactly that.
 *
 * A 500 here is not a security hole -- the queries are parameterised, and
 * nothing leaked -- but it is a page an ordinary user can break by editing a
 * URL, and it costs a database round trip and a logged stack trace every
 * time somebody does.
 *
 * The rule is CLAMP, NOT REJECT. A page number out of range is not an attack
 * and not worth an error page; the first page is what the reader wanted. An
 * id, on the other hand, is refused outright: asking for record -1 is a
 * mistake, and answering with record 1 would be worse than answering nothing.
 */

/** A 1-based page number. Anything unusable becomes 1. */
function pageOf(value, fallback = 1) {
    const page = Number.parseInt(value, 10);
    if (!Number.isFinite(page) || page < 1) return fallback;
    // A page number this large can only be a fuzzer or a typo, and the
    // OFFSET it produces is past anything the table will ever hold.
    return Math.min(page, 1000000);
}

/** A row count per page, clamped into something the database can serve. */
function limitOf(value, fallback = 20, max = 1000) {
    const limit = Number.parseInt(value, 10);
    if (!Number.isFinite(limit) || limit < 1) return fallback;
    return Math.min(limit, max);
}

/**
 * An INTEGER id from a URL, or null when it is not one.
 *
 * Postgres `integer` tops out at 2147483647. A larger number is not a big id,
 * it is a value of the wrong type, and sending it to the database earns
 * "value out of range for type integer" -- a 500 for what should be a 404.
 * Verified: /chat/channels/99999999999999999999/messages did that.
 */
const PG_INT_MAX = 2147483647;
function idOf(value) {
    const id = Number(value);
    if (!Number.isInteger(id) || id < 1 || id > PG_INT_MAX) return null;
    return id;
}

/** A bigint id -- messages use one, and it has its own ceiling. */
function bigIdOf(value) {
    const text = String(value === undefined || value === null ? "" : value).trim();
    if (!/^\d{1,19}$/.test(text)) return null;
    const id = Number(text);
    if (!Number.isSafeInteger(id) || id < 1) return null;
    return id;
}

/**
 * Text from a client, safe to put in a query.
 *
 * A NUL byte is the one character Postgres will not accept inside a text
 * value at all -- it answers "invalid message format" and the request 500s.
 * It cannot appear in any real employee id or search term, so it is stripped
 * rather than made into an error. Verified: /admin/config/%00 did that.
 */
function textOf(value, maxLength = 200) {
    if (value === undefined || value === null) return "";
    return String(value).split("\u0000").join("").slice(0, maxLength);
}

module.exports = { pageOf, limitOf, idOf, bigIdOf, textOf, PG_INT_MAX };
