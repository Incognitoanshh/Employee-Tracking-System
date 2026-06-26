// config/db.js
require("dotenv").config();
const { Pool, types } = require("pg");

// ✅ Timestamps as string — IST conversion client side hogi
types.setTypeParser(1114, (val) => val); // timestamp without tz
types.setTypeParser(1184, (val) => val); // timestamp with tz

const pool = new Pool({
    host:     process.env.DB_HOST,
    port:     process.env.DB_PORT     || 5432,
    database: process.env.DB_NAME,
    user:     process.env.DB_USER,
    password: process.env.DB_PASSWORD,
    // ✅ Connection pool limits
    max:             10,
    idleTimeoutMillis: 30000,
    connectionTimeoutMillis: 5000,
});

// Startup connection check
pool.query("SELECT NOW()")
    .then(r  => console.log("✅ DB connected:", r.rows[0].now))
    .catch(e => console.error("❌ DB connection failed:", e.message));

module.exports = pool;