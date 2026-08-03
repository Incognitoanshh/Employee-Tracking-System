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
// ── POOL SIZING (1000+ employees) ────────────────────────────────────────
//  node-pg ke defaults production ke liye khatarnak hain:
//    max                     = 10   (bahut kam)
//    connectionTimeoutMillis = 0    (pool bharne pe request HAMESHA ke liye
//                                    HANG — error bhi nahi, silent freeze)
//
//  Load: 1000 employees har 5s /config/sync maarte hain = ~200 req/s. Aur
//  /dashboard/me ka Promise.all EK SAATH 4 connections maangta hai — max 10
//  pe sirf 2 concurrent requests ban paati thin, baaki queue me atak jaatin.
//
//  Ab max 25 (Postgres default max_connections 100 hai; 25 ek instance ke
//  liye safe hai aur pm2 cluster me bhi jagah chhodta hai), aur 5s ka
//  connection timeout taaki overload pe request FAIL ho — hang na kare
//  (hang hone se client 30s tak baitha rehta aur retry storm ban jaata).
// ─────────────────────────────────────────────────────────────────────────
const pool = new Pool({

    max: parseInt(process.env.DB_POOL_MAX || "25", 10),
    idleTimeoutMillis: 30000,
    connectionTimeoutMillis: 5000,
    statement_timeout: 15000,      // ek slow query poore pool ko na roke

    options: "-c timezone=UTC",

    host: process.env.DB_HOST,

    port: process.env.DB_PORT,

    database: process.env.DB_NAME,

    user: process.env.DB_USER,

    password: process.env.DB_PASSWORD

});

// Pool-level errors (idle client ka connection toot jaana) — bina handler
// ke ye `error` event process crash kar deta hai. PM2 restart kar dega
// lekin us waqt ki saari requests fail ho jaayengi.
pool.on("error", (err) => {
    console.error("[DB POOL] idle client error:", err.message);
});

module.exports = pool;