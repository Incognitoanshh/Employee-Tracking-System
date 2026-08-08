/**
 * Passwords must not reach the server log.
 *
 * The error handler logs the request body, which is genuinely useful — except
 * on /auth/login, where the body holds a working password in the clear. These
 * lines go to PM2's log files on the VPS and stay there, so any error thrown
 * anywhere downstream of a parsed login wrote a live credential to disk, to be
 * read by anyone who can read a log file or receives one while debugging.
 *
 * Nothing about that fails loudly, which is why it needs a test rather than
 * care: a new route taking a password would reintroduce it silently.
 *
 * Run:  node server/tests/test_log_redaction.js
 */

const path = require("path");
const fs = require("fs");

let failures = 0;
function check(label, ok, detail = "") {
    if (!ok) failures += 1;
    console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${ok || !detail ? "" : `  — ${detail}`}`);
}

// server.js exits when its environment is not set and opens a port when it
// is, so the function is read out of the source and evaluated on its own.
// The alternative — booting the whole server — makes this test need a
// database in order to check a piece of string handling.
const source = fs.readFileSync(
    path.join(__dirname, "..", "server.js"), "utf8");

const start = source.indexOf("const SECRET_FIELDS");
const end = source.indexOf("// Advanced error formatter");
if (start < 0 || end < 0 || end < start) {
    console.log("  FAIL  the redaction helper is still in server.js");
    process.stdout.write("", () => process.exit(1));
}
const redactForLog = eval(`(function () {\n${source.slice(start, end)}\nreturn redactForLog;\n})()`);

console.log("A login that goes wrong");

let out = redactForLog({ username: "manager", password: "Demo1234" });
check("the password does not reach the log",
    !JSON.stringify(out).includes("Demo1234"), JSON.stringify(out));
check("but the username does, or the line is useless",
    out.username === "manager", JSON.stringify(out));
check("and it says something was taken out, rather than hiding the field",
    out.password === "[redacted]", JSON.stringify(out));

console.log("\nThe other ways a secret arrives");

out = redactForLog({
    old_password: "a", new_password: "b", confirm_password: "c",
    token: "eyJhbGciOi", encryption_key: "0".repeat(64),
});
check("a password change carries three of them, and none survives",
    !/["'](a|b|c)["']/.test(JSON.stringify(out)), JSON.stringify(out));
check("a token is a credential too",
    !JSON.stringify(out).includes("eyJhbGciOi"), JSON.stringify(out));
check("so is the encryption key — it decrypts every screenshot ever taken",
    !JSON.stringify(out).includes("0000"), JSON.stringify(out));

console.log("\nSecrets that are not at the top level");

out = redactForLog({ employee: { username: "amit", password: "hunter2" } });
check("one nested inside another object is still removed",
    !JSON.stringify(out).includes("hunter2"), JSON.stringify(out));

out = redactForLog({ users: [{ password: "p1" }, { password: "p2" }] });
check("and one inside a list",
    !JSON.stringify(out).includes("p1") && !JSON.stringify(out).includes("p2"),
    JSON.stringify(out));

out = redactForLog({ Password: "Caps123" });
check("the field name is matched whatever its capitalisation",
    !JSON.stringify(out).includes("Caps123"), JSON.stringify(out));

console.log("\nIt must not break the logging it is part of");

check("no body at all is fine", redactForLog(undefined) === undefined);
check("an empty body is fine", JSON.stringify(redactForLog({})) === "{}");
check("ordinary fields pass through untouched",
    redactForLog({ employee_id: "EM101", seq: 42 }).employee_id === "EM101");

// A body that refers to itself would otherwise recurse until the process
// dies — inside the error handler, which is the one place that must never
// throw.
const loop = { name: "x" };
loop.self = loop;
let survived = true;
try { redactForLog(loop); } catch (_) { survived = false; }
check("a self-referencing body does not take the error handler down with it",
    survived, "redaction recursed until it crashed");

console.log();
if (failures) {
    console.log(`${failures} failure(s)`);
    process.stdout.write("", () => process.exit(1));
} else {
    console.log("all log redaction checks passed");
    process.stdout.write("", () => process.exit(0));
}
