const { Pool, types } = require("pg");
require("dotenv").config();

// ─────────────────────────────────────────────────────────────
types.setTypeParser(1114, (val) => val); // timestamp without time zone
types.setTypeParser(1184, (val) => val); // timestamp with time zone

const pool = new Pool({

    host: process.env.DB_HOST,

    port: process.env.DB_PORT,

    database: process.env.DB_NAME,

    user: process.env.DB_USER,

    password: process.env.DB_PASSWORD

});

module.exports = pool;