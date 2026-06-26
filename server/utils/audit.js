const pool = require("../config/db");

const logAudit = async (employee_id, activity) => {
    try {
        await pool.query(
            `INSERT INTO audit_logs (employee_id, activity, created_at)
             VALUES ($1, $2, NOW())`,
            [employee_id, activity]
        );
        console.log(`[AUDIT] emp=${employee_id || "SYSTEM"}: ${activity}`);
    } catch (err) {
        console.error("[AUDIT LOG ERROR]", err.message);
    }
};

module.exports = { logAudit };
