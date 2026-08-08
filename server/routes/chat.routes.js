const express  = require("express");
const multer   = require("multer");
const path     = require("path");
const router   = express.Router();
const chatCtrl = require("../controllers/chat.controller");

// Mounted behind verifyToken in server.js. Every route here is for the person
// signed in — there is no role gate, because the access rule is membership,
// not rank: an admin who is not in a team sees nothing here either, and a
// super admin reads other people's conversations through the audited route in
// admin.routes, never through this.

// ── file uploads ───────────────────────────────────────────────────────────
//  The bytes arriving here are already encrypted by the client, the same way
//  screenshots are, so the server stores something it cannot read.
//
//  The filename is generated, never taken from the request. `originalname`
//  is client-controlled, and using it as a path is how directory traversal
//  happens — the screenshot upload had that bug once already.
const storage = multer.diskStorage({
    destination: (req, file, cb) => cb(null, chatCtrl.attachmentDir()),
    filename: (req, file, cb) => {
        const who = (req.employee?.employee_id || "unknown").replace(/[^a-zA-Z0-9._-]/g, "_");
        const ext = path.extname(file.originalname || "").replace(/[^a-zA-Z0-9.]/g, "").slice(0, 10);
        cb(null, `${who}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}${ext}`);
    },
});

const upload = multer({
    storage,
    limits: { fileSize: chatCtrl.MAX_ATTACHMENT_BYTES, files: 1 },
});

// What I can see, and how much of it is unread.
router.get("/me/teams", chatCtrl.getMyTeams);

// The poll. Everything after ?since=<seq>, across every visible channel.
router.get("/updates", chatCtrl.getUpdates);

// Search, before /channels/:id so that neither can shadow the other.
router.get("/search", chatCtrl.search);

// One channel.
router.get("/channels/:id/messages",  chatCtrl.getMessages);
router.post("/channels/:id/messages", chatCtrl.sendMessage);
router.get("/channels/:id/members",   chatCtrl.getChannelMembers);
router.post("/channels/:id/read",     chatCtrl.markRead);
router.get("/channels/:id/pinned",    chatCtrl.getPinned);

// Files. Uploaded first, then named by the message that carries them.
router.post("/channels/:id/attachments",
    upload.single("file"), chatCtrl.uploadAttachment);
router.get("/attachments/:id", chatCtrl.downloadAttachment);

// Editing is allowed for a few minutes and keeps every previous version.
router.patch("/messages/:seq", chatCtrl.editMessage);
router.post("/messages/:seq/pin", chatCtrl.setPinned);
// Withdraws a message from view. The row and its text stay — see the note on
// deleteMessage for why this is not a DELETE in the database.
router.delete("/messages/:seq", chatCtrl.deleteMessage);

router.post("/notifications/read", chatCtrl.markNotificationsRead);

// Multer rejects a file before any handler runs — an over-sized upload would
// otherwise fall through to the generic error handler and come back as a 500,
// which tells the employee nothing about the actual limit.
router.use((error, req, res, next) => {
    if (error && error.code === "LIMIT_FILE_SIZE") {
        const mb = Math.round(chatCtrl.MAX_ATTACHMENT_BYTES / (1024 * 1024));
        return res.status(413).json({
            success: false,
            message: `That file is too large — the limit is ${mb} MB.`,
        });
    }
    return next(error);
});

module.exports = router;
