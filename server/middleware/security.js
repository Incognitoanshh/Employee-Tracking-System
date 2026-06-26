const helmet = require('helmet');
const rateLimit = require('express-rate-limit');

const limiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    max: 100,
    message: { success: false, message: "Too many requests" }
});

module.exports = { helmet, limiter };
