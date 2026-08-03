const { Pool, types } = require("pg");
require("dotenv").config();

// ─────────────────────────────────────────────────────────────
types.setTypeParser(1114, (val) => val); // timestamp without time zone
types.setTypeParser(1184, (val) => val); // timestamp with time zone

// BUG FIX (attendance times off by hours):
// `attendance.login_time` / `logout_time` `TIMESTAMP WITHOUT TIME ZONE`
// columns hain, aur inme `NOW()` (jo timestamptz hai) insert hota hai.
// Postgres us conversion ke liye SESSION ki timezone use karta hai. Yaani
// stored value chupchaap us TZ pe depend karti hai jo DB/OS pe set ho.
// Poora client code (attendance_window.py, logs_window.py, dashboard) in
// values ko UTC MAAN kar IST me convert karta hai — agar server ki TZ UTC
// nahi hui to har login/logout time ghanton shift ho jaata hai aur
// Today/Week/Month buckets galat din me chale jaate hain.
//
// Har connection pe explicitly UTC set karke ye ambiguity poori tarah
// khatam kar dete hain — ab ye VPS/OS/postgresql.conf ki setting pe
// depend nahi karta.
const pool = new Pool({

    options: "-c timezone=UTC",

    host: process.env.DB_HOST,

    port: process.env.DB_PORT,

    database: process.env.DB_NAME,

    user: process.env.DB_USER,

    password: process.env.DB_PASSWORD

});

module.exports = pool;