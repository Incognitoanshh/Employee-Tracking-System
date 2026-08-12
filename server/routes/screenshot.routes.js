const express = require("express");
const multer  = require("multer");
const path    = require("path");
const fs      = require("fs");
const crypto  = require("crypto");
const router  = express.Router();

const screenshotController = require("../controllers/screenshot.controller");

// uploads/screenshots directory ensure karo
const uploadDir = process.env.UPLOAD_DIR
    ? path.resolve(process.env.UPLOAD_DIR)
    : path.join(__dirname, "../uploads/screenshots");
if (!fs.existsSync(uploadDir)) {
    fs.mkdirSync(uploadDir, { recursive: true });
}

const storage = multer.diskStorage({
    destination: (req, file, cb) => {
        cb(null, uploadDir);
    },
    filename: (req, file, cb) => {
        const emp = req.employee?.employee_id || "unknown";
        // SECURITY FIX: file.originalname client-controlled hota hai. Pehle
        // seedha concat ho raha tha — agar usme "../" ho toh upload dir se
        // escape ho sakta tha (path traversal). Ab sirf safe chars allow.
        const safeName = path.basename(file.originalname).replace(/[^a-zA-Z0-9._-]/g, "_");
        // A RANDOM PART, not just the clock.
        //
        // BUG this fixes: the name was employee + Date.now() + original name.
        // Date.now() has millisecond resolution, and the retry path uploads
        // under `<screenshot_id>.enc` — the same name the first attempt used.
        // So a retry racing the original, or any two uploads landing in the
        // same millisecond, produced the SAME stored filename: the second
        // file overwrote the first, two database rows pointed at one file,
        // and one capture was gone. The rows still looked fine, which is why
        // nobody would ever have noticed.
        //
        // Reproduced by two concurrent uploads of the same name in
        // test_concurrency before this line existed.
        const unique = crypto.randomBytes(6).toString("hex");
        cb(null, `${emp}-${Date.now()}-${unique}-${safeName}`);
    }
});

const upload = multer({
    storage,
    limits: { fileSize: 10 * 1024 * 1024 }, // 10MB max
    fileFilter: (req, file, cb) => {
        // Images allowed + .enc (AES-GCM encrypted screenshots from client)
        const isImage = file.mimetype.startsWith("image/");
        const isEnc   = file.originalname.endsWith(".enc") ||
                        file.mimetype === "application/octet-stream";
        if (isImage || isEnc) {
            cb(null, true);
        } else {
            cb(new Error("Only image or encrypted screenshot files allowed"), false);
        }
    }
});

router.post("/upload",         upload.single("screenshot"), screenshotController.uploadScreenshot);
router.get("/all",             screenshotController.getScreenshots);
router.get("/download/:id",    screenshotController.downloadScreenshot);

// Multer rejects an over-sized file BEFORE any handler runs, and the
// rejection travels as an error — which fell through to the generic handler
// and came back as a 500. A capture on a very large or multi-monitor screen
// is an ordinary thing to run into, and "Internal server error" tells the
// client nothing, so it retried the same file forever.
//
// The chat attachment route already did this; the screenshot route did not.
router.use((error, req, res, next) => {
    if (error && error.code === "LIMIT_FILE_SIZE") {
        return res.status(413).json({
            success: false,
            error: "Screenshot too large — 10 MB is the limit",
        });
    }
    if (error) {
        return res.status(400).json({
            success: false,
            error: error.message || "Upload rejected",
        });
    }
    return next();
});

module.exports = router;
