/**
 * The alert rules, judged against invented days.
 *
 * These are the decisions, so this is where the thinking is checked. The
 * things that would make the feature useless are all here rather than in the
 * SQL:
 *
 *   * Firing on somebody's weekly off, or on a holiday. An alert list that
 *     shouts every Sunday is one nobody reads by the second week, and after
 *     that the alert that mattered is lost in it.
 *   * Inventing a shift for an employee who has none. The owner of this
 *     system decides shifts; code must not guess one in order to have
 *     something to complain about.
 *   * Two alerts for one cause — "no data for three days" AND "did not log
 *     in today" about the same silent laptop.
 *   * Thresholds baked into the code instead of read from settings.
 *
 * Run:  node server/tests/test_alert_rules.js
 */

const rules = require("../utils/alert_rules");

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}

// A Wednesday. Sunday off (7), no holidays unless a test adds one.
const WEDNESDAY = "2026-08-05";
const SUNDAY = "2026-08-09";

function person(extra = {}) {
    return {
        employee_id: "EM101",
        username: "rajesh",
        full_name: "Rajesh Kumar",
        suspended: false,
        shift_start: "09:00:00",
        late_grace_minutes: 10,
        weekly_offs: "7",
        ...extra,
    };
}

function facts(extra = {}) {
    return {
        employee: person(extra.employee || {}),
        settings: {},
        holidays: new Set(),
        isoDate: WEDNESDAY,
        nowMinutes: 12 * 60,          // midday
        lastSeenMinutes: 5,
        loggedInToday: true,
        idleMinutes: 0,
        ...extra,
    };
}

console.log("An app that has gone quiet");

check("a heartbeat five minutes ago is not an alert",
    rules.forEmployee(facts()).length === 0,
    JSON.stringify(rules.forEmployee(facts())));

let out = rules.forEmployee(facts({ lastSeenMinutes: 60 * 30, loggedInToday: false }));
check("thirty hours of silence is",
    out.some((a) => a.type === "NOT_REPORTING"), JSON.stringify(out.map((a) => a.type)));
check("and it is the loudest thing on the list",
    out[0].severity === "HIGH", JSON.stringify(out[0]));
check("it says how long, not just that something is wrong",
    /1 d/.test(out[0].title), out[0].title);

check("a silent app does NOT also get told off for not logging in",
    !out.some((a) => a.type === "NO_LOGIN"),
    "two alerts for one cause: " + JSON.stringify(out.map((a) => a.type)));

out = rules.forEmployee(facts({ lastSeenMinutes: null }));
check("an account that has never reported at all is called out separately",
    out.some((a) => a.type === "NEVER_REPORTED"), JSON.stringify(out.map((a) => a.type)));

out = rules.forEmployee(facts({
    lastSeenMinutes: 60 * 30, settings: { alert_silent_hours: 72 } }));
check("the silence threshold comes from settings, not the code",
    out.length === 0, JSON.stringify(out.map((a) => a.type)));

console.log("\nShift started, nobody logged in");

out = rules.forEmployee(facts({ loggedInToday: false, nowMinutes: 9 * 60 + 30 }));
check("nine-thirty, with ten minutes grace and thirty more, is not yet late",
    out.length === 0, JSON.stringify(out.map((a) => a.title)));

out = rules.forEmployee(facts({ loggedInToday: false, nowMinutes: 9 * 60 + 45 }));
check("nine forty-five is",
    out.some((a) => a.type === "NO_LOGIN"), JSON.stringify(out.map((a) => a.type)));
check("and it names the shift it is measuring against",
    /09:00/.test(out[0].detail), out[0].detail);

out = rules.forEmployee(facts({ loggedInToday: true, nowMinutes: 18 * 60 }));
check("somebody who did log in is never chased",
    out.length === 0, JSON.stringify(out.map((a) => a.type)));

out = rules.forEmployee(facts({
    loggedInToday: false, nowMinutes: 18 * 60, employee: { shift_start: null } }));
check("an employee with NO shift configured is left alone — the code does not invent one",
    out.length === 0, JSON.stringify(out.map((a) => a.type)));

out = rules.forEmployee(facts({
    loggedInToday: false, nowMinutes: 18 * 60, isoDate: SUNDAY }));
check("nobody is chased on their weekly off",
    !out.some((a) => a.type === "NO_LOGIN"), JSON.stringify(out.map((a) => a.type)));

out = rules.forEmployee(facts({
    loggedInToday: false, nowMinutes: 18 * 60, holidays: new Set([WEDNESDAY]) }));
check("nor on a holiday",
    !out.some((a) => a.type === "NO_LOGIN"), JSON.stringify(out.map((a) => a.type)));

out = rules.forEmployee(facts({
    loggedInToday: false, nowMinutes: 9 * 60 + 45,
    settings: { alert_late_login_minutes: 120 } }));
check("how late is late comes from settings too",
    out.length === 0, JSON.stringify(out.map((a) => a.type)));

out = rules.forEmployee(facts({
    loggedInToday: false, nowMinutes: 9 * 60 + 45,
    employee: { late_grace_minutes: 60 } }));
check("and the shift's own grace period is respected on top of it",
    out.length === 0, JSON.stringify(out.map((a) => a.type)));

console.log("\nToo much idle");

out = rules.forEmployee(facts({ idleMinutes: 100 }));
check("under the limit, nothing is said", out.length === 0, JSON.stringify(out));

out = rules.forEmployee(facts({ idleMinutes: 200 }));
check("over it, one quiet alert",
    out.length === 1 && out[0].type === "HIGH_IDLE", JSON.stringify(out.map((a) => a.type)));
check("quiet on purpose — idle is a reason to look, not to act",
    out[0].severity === "LOW", out[0].severity);
check("and it reads as time, not seconds",
    /3 hr/.test(out[0].title), out[0].title);

out = rules.forEmployee(facts({ idleMinutes: 200, isoDate: SUNDAY }));
check("idle on a day off is not counted against anybody",
    out.length === 0, JSON.stringify(out.map((a) => a.type)));

out = rules.forEmployee(facts({ idleMinutes: 200, settings: { alert_idle_minutes: 300 } }));
check("the idle limit is a setting as well",
    out.length === 0, JSON.stringify(out.map((a) => a.type)));

console.log("\nWho is exempt, and the master switch");

out = rules.forEmployee(facts({
    employee: { suspended: true }, lastSeenMinutes: 60 * 100, loggedInToday: false }));
check("a suspended account raises nothing — that was already somebody's decision",
    out.length === 0, JSON.stringify(out.map((a) => a.type)));

out = rules.forEmployee(facts({
    lastSeenMinutes: 60 * 100, loggedInToday: false, settings: { alerts_enabled: "false" } }));
check("and the whole feature can be switched off",
    out.length === 0, JSON.stringify(out.map((a) => a.type)));

console.log("\nOrder and wording");

out = rules.forEmployee(facts({
    lastSeenMinutes: 60 * 30, loggedInToday: false, idleMinutes: 500 }));
check("the worst thing is first",
    out[0].severity === "HIGH", JSON.stringify(out.map((a) => a.severity)));

check("45 minutes reads as minutes", rules.describeGap(45) === "45 min", rules.describeGap(45));
check("200 minutes reads as hours", rules.describeGap(200) === "3 hr 20 min", rules.describeGap(200));
check("two days reads as days", rules.describeGap(60 * 50) === "2 d 2 hr", rules.describeGap(60 * 50));

console.log("\nSettings that arrive broken");

check("an unsaved setting falls back to the default",
    rules.setting({}, "alert_idle_minutes") === rules.DEFAULTS.alert_idle_minutes);
check("an empty string is not treated as zero — that would alert on everybody",
    rules.setting({ alert_idle_minutes: "" }, "alert_idle_minutes")
        === rules.DEFAULTS.alert_idle_minutes,
    String(rules.setting({ alert_idle_minutes: "" }, "alert_idle_minutes")));
check("nor is nonsense",
    rules.setting({ alert_idle_minutes: "soon" }, "alert_idle_minutes")
        === rules.DEFAULTS.alert_idle_minutes);
check("a saved number is used",
    rules.setting({ alert_idle_minutes: "90" }, "alert_idle_minutes") === 90);

console.log();
if (failures) {
    console.log(`${failures} failure(s)`);
    process.stdout.write("", () => process.exit(1));
} else {
    console.log("all alert rule checks passed");
    process.stdout.write("", () => process.exit(0));
}
