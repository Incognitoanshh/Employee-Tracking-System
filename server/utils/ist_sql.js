/**
 * SQL fragments for converting stored UTC timestamps to the IST calendar day.
 *
 * WHY THIS EXISTS
 * Every timestamp column is TIMESTAMP WITHOUT TIME ZONE holding UTC (see the
 * `options: "-c timezone=UTC"` note in config/db.js). Anywhere a query needs
 * "which IST day did this happen on", it has to convert first — and that
 * conversion was written out by hand in eleven places across three
 * controllers.
 *
 * All eleven were identical, so nothing was broken. The risk is the one that
 * already bit the client: someone changes the rule in one place and misses
 * the rest. On the client that split-brain produced 130 captures where 10
 * were configured, and later a whole session with zero. This is the same
 * shape of hazard, one layer over.
 *
 * The client has its own IST handling in client/core/time_ist.py. That is
 * NOT duplication of this — the server owns the shift data, the client owns
 * scheduling against it. They are different responsibilities that happen to
 * share a timezone.
 */

/** Columns and expressions this module is willing to embed in SQL. */
const SAFE_EXPRESSION = /^(NOW\(\)|[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?)$/i;

/**
 * `DATE(expr AT UTC AT IST)` — the IST calendar day an instant falls on.
 *
 * @param {string} expr A column name, an optionally table-qualified column,
 *   or the literal `NOW()`. Every current caller passes a hardcoded string;
 *   the check exists so that stays true. This is string-built SQL, so a
 *   caller reaching for a request parameter here has to be stopped at the
 *   door rather than trusted.
 */
function istDate(expr) {
    const value = String(expr).trim();
    if (!SAFE_EXPRESSION.test(value)) {
        throw new Error(
            `istDate() refuses ${JSON.stringify(expr)}: only a column name or NOW() ` +
            `may be interpolated into SQL. Pass values as query parameters instead.`
        );
    }
    return `DATE((${value} AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')`;
}

/**
 * Today's IST calendar day.
 *
 * NOT istDate("NOW()") — and this is the whole reason the helper exists.
 *
 * `AT TIME ZONE` does two opposite things depending on what it is given:
 *   - applied to a NAIVE timestamp it INTERPRETS the value as being in that
 *     zone and returns timestamptz;
 *   - applied to a timestamptz it CONVERTS to that zone and returns naive.
 *
 * Every timestamp column here is naive UTC, so `(col AT UTC) AT IST`
 * interprets-then-converts and is correct. But NOW() is already timestamptz,
 * so `(NOW() AT UTC) AT IST` converts-then-*re-labels* — it takes the UTC
 * wall-clock and calls it IST, which is not a conversion at all.
 *
 * Measured on a session with timezone=UTC at 21:04 UTC (02:34 IST):
 *     DATE((NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')  -> 2026-08-04
 *     DATE(NOW() AT TIME ZONE 'Asia/Kolkata')                       -> 2026-08-05
 *
 * The old inline SQL used the first form. Between 18:30 and 24:00 UTC —
 * 00:00 to 05:30 IST, five and a half hours every day — it compared a
 * correctly converted column date against the UTC date and matched nothing,
 * so the dashboard's "today" figures read zero.
 */
function istToday() {
    return `DATE(NOW() AT TIME ZONE 'Asia/Kolkata')`;
}

/**
 * `<column> falls on today in IST` — the comparison most callers actually
 * want, so they do not have to remember to convert both sides.
 */
function isTodayIST(column) {
    return `${istDate(column)} = ${istToday()}`;
}

module.exports = { istDate, istToday, isTodayIST };
