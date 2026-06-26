const express = require("express");
const multer  = require("multer");
const fs      = require("fs");
const path    = require("path");

const router               = express.Router();
const screenshotController = require("../controllers/screenshot.controller");

// ✅ Upload directory ensure
const UPLOAD_DIR = path.join(__dirname, "../uploads/screenshots");
if (!fs.existsSync(UPLOAD_DIR)) {
    fs.mkdirSync(UPLOAD_DIR, { recursive: true });
}

const storage = multer.diskStorage({
    destination: (_req, _file, cb) => cb(null, UPLOAD_DIR),
    filename:    (_req,  file, cb) => {
        const safe = path.basename(file.originalname);
        cb(null, `${Date.now()}-${safe}`);
    },
});

// ✅ File type filter — sirf encrypted files
const fileFilter = (_req, file, cb) => {
    const allowed = [".enc", ".bin", ".jpg", ".png"];
    const ext     = path.extname(file.originalname).toLowerCase();
    if (allowed.includes(ext)) {
        cb(null, true);
    } else {
        cb(new Error(`File type not allowed: ${ext}`), false);
    }
};

const upload = multer({
    storage,
    fileFilter,
    limits: { fileSize: 10 * 1024 * 1024 }, // ✅ 10MB max
});

// ✅ verifyToken server.js mein already lagaya hai
// Routes mein dobara nahi — double verify avoid
router.post(
    "/upload",
    upload.single("screenshot"),
    screenshotController.uploadScreenshot
);

router.get("/all",           screenshotController.getScreenshots);
router.get("/download/:id",  screenshotController.downloadScreenshot);

module.exports = router;