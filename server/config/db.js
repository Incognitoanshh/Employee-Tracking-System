const { Pool, types } = require("pg");
require("dotenv").config();

// ─────────────────────────────────────────────────────────────
types.setTypeParser(1114, (val) => val); // timestamp without time zone
types.setTypeParser(1184, (val) => val); // timestamp with time zone

// BUG FIX (attendance times off by hours):
// `attendance.login_time` / `logout_time` are TIMESTAMP WITHOUT TIME ZONE
// columns, and NOW() (a timestamptz) is inserted into them. Postgres uses
// the SESSION timezone for that conversion, so the stored value silently
// depends on whatever TZ the database or OS happens to have.
//
// The whole client (attendance_window.py, logs_window.py, dashboard)
// treats these values as UTC and converts them to IST. If the server TZ
// is not UTC, every login/logout time shifts by hours and the
// Today/Week/Month buckets land on the wrong day.
//
// Setting UTC explicitly on every connection removes the ambiguity — this
// no longer depends on the VPS, the OS, or postgresql.conf.
// ── POOL SIZING (1000+ employees) ────────────────────────────────────────
//  node-pg's defaults are dangerous in production:
//    max                     = 10   (far too low)
//    connectionTimeoutMillis = 0    (when the pool is full a request HANGS
//                                    forever — no error, just a silent freeze)
//
//  Load: 1000 employees hitting /config/sync every 5s is ~200 req/s, and
//  the Promise.all in /dashboard/me asks for 4 connections at once. At
//  max 10 only 2 requests could run concurrently; the rest queued.
//
//  Now max 25 (Postgres defaults to max_connections 100; 25 is safe for one
//  instance and leaves room for a pm2 cluster), plus a 5s connection
//  timeout so an overloaded request FAILS instead of hanging — a hang keeps
//  the client waiting 30s and turns into a retry storm.
// ─────────────────────────────────────────────────────────────────────────
const pool = new Pool({

    max: parseInt(process.env.DB_POOL_MAX || "25", 10),
    idleTimeoutMillis: 30000,
    connectionTimeoutMillis: 5000,
    statement_timeout: 15000,      // stop one slow query from blocking the pool

    options: "-c timezone=UTC",

    host: process.env.DB_HOST,

    port: process.env.DB_PORT,

    database: process.env.DB_NAME,

    user: process.env.DB_USER,

    password: process.env.DB_PASSWORD

});

// Pool-level errors (an idle client losing its connection). Without a
// handler this `error` event crashes the process. PM2 would restart it,
// but every in-flight request fails in the meantime.
pool.on("error", (err) => {
    console.error("[DB POOL] idle client error:", err.message);
});

module.exports = pool;