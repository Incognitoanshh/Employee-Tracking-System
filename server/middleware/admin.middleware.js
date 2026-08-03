/**
 * Role middleware
 *
 * Role hierarchy:
 *   super_admin  — "god" role. Company ka owner/manager. Kisi ka bhi role
 *                  badal sakta hai, sab kuch manage kar sakta hai. Uske
 *                  upar KOI rule nahi lagta, aur use koi doosra
 *                  delete/demote/modify NAHI kar sakta.
 *   admin        — employees ko manage karta hai (1–20 log). Doosre admin
 *                  ya super_admin ko chhoo nahi sakta, sirf apne aap ko.
 *   employee     — koi admin access nahi.
 */

const ROLE_SUPER_ADMIN = "super_admin";
const ROLE_ADMIN = "admin";

/** admin ya super_admin dono allowed */
const adminOnly = (req, res, next) => {
    const role = req.employee?.role;
    if (role !== ROLE_ADMIN && role !== ROLE_SUPER_ADMIN) {
        return res.status(403).json({
            success: false,
            message: "Admin access required."
        });
    }
    next();
};

/** sirf super_admin */
const superAdminOnly = (req, res, next) => {
    if (req.employee?.role !== ROLE_SUPER_ADMIN) {
        return res.status(403).json({
            success: false,
            message: "Super admin access required."
        });
    }
    next();
};

/**
 * Kya `req.employee` (actor) `targetRole` wale `targetId` employee pe
 * koi write/管理 action kar sakta hai?
 *
 * Rules:
 *   - super_admin ko koi bhi chhoo nahi sakta, siwaay khud super_admin ke.
 *   - admin ko sirf super_admin (ya wo admin khud) modify kar sakta hai.
 *   - employee ko admin aur super_admin dono modify kar sakte hain.
 *
 * Returns null agar allowed hai, warna ek error message string.
 */
const canManage = (actor, targetId, targetRole) => {
    const actorRole = actor?.role;
    const actorId = actor?.employee_id;
    const isSelf = actorId && targetId && actorId === targetId;

    if (actorRole === ROLE_SUPER_ADMIN) {
        return null; // super admin pe koi restriction nahi
    }

    if (targetRole === ROLE_SUPER_ADMIN) {
        return "Super admin cannot be modified by other users.";
    }

    if (targetRole === ROLE_ADMIN && !isSelf) {
        return "Admins cannot modify other admin accounts.";
    }

    return null;
};

module.exports = {
    adminOnly,
    superAdminOnly,
    canManage,
    ROLE_SUPER_ADMIN,
    ROLE_ADMIN,
};
