const express = require("express");
const multer = require("multer");
const path = require("path");
const fs = require("fs");
const crypto = require("crypto");

const router = express.Router();
const profileCtrl = require("../controllers/profile.controller");

const PHOTO_DIR = process.env.PROFILE_PHOTO_DIR
    ? path.resolve(process.env.PROFILE_PHOTO_DIR)
    : path.resolve(__dirname, "../uploads/profile_photos");
fs.mkdirSync(PHOTO_DIR, { recursive: true });

const MAX_PHOTO_BYTES = 5 * 1024 * 1024;

// The filename is GENERATED, never taken from the request — the same rule the
// chat attachment route follows. A name from the client is a path waiting to
// be traversed, and two people uploading "photo.png" would otherwise overwrite
// each other.
const storage = multer.diskStorage({
    destination: (req, file, cb) => cb(null, PHOTO_DIR),
    filename: (req, file, cb) => {
        const extension = { "image/png": ".png", "image/jpeg": ".jpg" }[file.mimetype] || ".img";
        cb(null, `${crypto.randomUUID()}${extension}`);
    },
});

const upload = multer({
    storage,
    limits: { fileSize: MAX_PHOTO_BYTES },
    fileFilter: (req, file, cb) => {
        // Checked here rather than after writing: a file that is not going to
        // be accepted should never reach the disk at all.
        if (file.mimetype === "image/png" || file.mimetype === "image/jpeg") {
            return cb(null, true);
        }
        cb(new Error("Only PNG and JPEG images are allowed"));
    },
});

router.get("/me", profileCtrl.getMyProfile);
router.patch("/me", profileCtrl.updateMyProfile);
router.get("/me/sessions", profileCtrl.getMySessions);
router.get("/me/work-summary", profileCtrl.getMyWorkSummary);
router.post("/me/logout-all", profileCtrl.logoutEverywhere);

router.post("/me/photo", upload.single("photo"), profileCtrl.uploadMyPhoto);
router.delete("/me/photo", profileCtrl.deleteMyPhoto);
// The caller's own by default; an administrator may name somebody else, and
// the controller is what decides that. Two routes rather than an optional
// parameter: Express 5 rejects `:name?` outright.
// Proving the address. Both answer about the caller only — there is no id
// here either, so there is nothing to point at somebody else's mailbox.
router.post("/me/email/code", profileCtrl.sendEmailCode);
router.post("/me/email/verify", profileCtrl.verifyEmailCode);

router.get("/photo", profileCtrl.getPhoto);
router.get("/photo/:employee_id", profileCtrl.getPhoto);

// Multer rejects a file before any handler runs, and that rejection arrives
// as an error — which would fall through to the generic handler and come back
// as a 500. A photo that is too large or of the wrong type is an ordinary
// thing for somebody to do, and they need to be told which.
router.use((error, req, res, next) => {
    if (error && error.code === "LIMIT_FILE_SIZE") {
        return res.status(413).json({
            success: false,
            message: `That image is too large — ${MAX_PHOTO_BYTES / (1024 * 1024)} MB is the limit`,
        });
    }
    if (error) {
        return res.status(400).json({
            success: false,
            message: error.message || "Upload rejected",
        });
    }
    return next();
});

module.exports = router;
