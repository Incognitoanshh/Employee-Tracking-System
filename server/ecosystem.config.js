module.exports = {
    apps: [{
        name:             "ets-server",
        script:           "server.js",
        instances:        1,
        exec_mode:        "fork",
        watch:            false,
        max_memory_restart: "300M",
        env: {
            NODE_ENV: "production",
            PORT:     8000,
        },
        error_file:   "logs/err.log",
        out_file:     "logs/out.log",
        log_date_format: "YYYY-MM-DD HH:mm:ss",
        restart_delay:    3000,
        max_restarts:     10,
        min_uptime:       "10s",
    }]
};
