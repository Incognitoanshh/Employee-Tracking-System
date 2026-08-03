#!/usr/bin/env node
/**
 * Kisi bhi account ka username / password badalta hai.
 *
 * Server me change-password ka koi endpoint nahi hai (bcrypt hash sirf
 * create ke waqt banta hai), is liye ye seedha DB update karta hai —
 * usi bcrypt cost (10) ke saath jo admin.controller.js use karta hai.
 *
 * Password TYPE karke poocha jaata hai aur screen pe dikhta nahi. Wo
 * kabhi argv me nahi jaata, is liye na shell history me aata hai, na
 * `ps` me, na kisi file me.
 *
 * Chalane ka tarika (server folder ke andar se, taaki .env mile):
 *     node scripts/change_credentials.js EMP001
 *
 * Username same rakhna ho to us prompt pe seedha Enter daba dein.
 */
const bcrypt   = require("bcryptjs");
const readline = require("readline");
const pool     = require("../config/db");

function ask(question, { hidden = false } = {}) {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    return new Promise((resolve) => {
        if (!hidden) return rl.question(question, (a) => { rl.close(); resolve(a); });

        // Hidden input — typed characters echo nahi hote
        const onData = (char) => {
            if ([ "\n", "\r", "" ].includes(char.toString())) {
                process.stdin.removeListener("data", onData);
                return;
            }
            readline.clearLine(process.stdout, 0);
            readline.cursorTo(process.stdout, 0);
            process.stdout.write(question);
        };
        process.stdin.on("data", onData);
        rl.question(question, (a) => { rl.close(); process.stdout.write("\n"); resolve(a); });
    });
}

(async () => {
    const employeeId = process.argv[2];
    if (!employeeId) {
        console.error("Usage: node scripts/change_credentials.js <employee_id>");
        process.exit(2);
    }

    const { rows } = await pool.query(
        "SELECT employee_id, username, role FROM employees WHERE employee_id = $1",
        [employeeId]
    );
    if (!rows.length) {
        console.error(`❌ ${employeeId} naam ka koi employee nahi mila.`);
        process.exit(1);
    }
    const emp = rows[0];
    console.log(`\nAccount : ${emp.employee_id}`);
    console.log(`Username: ${emp.username}`);
    console.log(`Role    : ${emp.role}\n`);

    const newUser = (await ask(`Naya username (Enter = "${emp.username}" hi rehne do): `)).trim();
    const pw1 = await ask("Naya password           : ", { hidden: true });
    const pw2 = await ask("Naya password (dobara)  : ", { hidden: true });

    if (pw1 !== pw2) {
        console.error("❌ Dono password match nahi kiye. Kuch nahi badla.");
        process.exit(1);
    }
    if (pw1.length < 12) {
        console.error("❌ Password kam se kam 12 character ka rakho. Kuch nahi badla.");
        process.exit(1);
    }

    const username = newUser || emp.username;

    // Username unique hona chahiye — login isi pe chalta hai. Iske bina
    // duplicate username ban jaata aur login galat account uthata.
    if (username !== emp.username) {
        const clash = await pool.query(
            "SELECT employee_id FROM employees WHERE username = $1 AND employee_id <> $2",
            [username, employeeId]
        );
        if (clash.rows.length) {
            console.error(`❌ Username "${username}" already ${clash.rows[0].employee_id} use kar raha hai.`);
            process.exit(1);
        }
    }

    const hash = await bcrypt.hash(pw1, 10);   // wahi cost jo admin.controller use karta hai
    await pool.query(
        "UPDATE employees SET username = $1, password = $2 WHERE employee_id = $3",
        [username, hash, employeeId]
    );

    // Chal rahe sessions kaat do — warna purane credentials wala token
    // expire hone tak chalta rehta hai, yaani password badalne ka
    // asar turant nahi hota.
    await pool.query(
        "UPDATE active_sessions SET token = NULL WHERE employee_id = $1",
        [employeeId]
    );

    console.log(`\n✅ ${employeeId} update ho gaya.`);
    console.log(`   Username: ${username}`);
    console.log(`   Password: badal diya gaya (hashed)`);
    console.log(`   Sessions: kaat diye gaye — dobara login karna padega\n`);
    await pool.end();
})().catch((e) => {
    console.error("❌", e.message);
    process.exit(1);
});
