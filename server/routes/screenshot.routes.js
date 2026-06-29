const express = require("express");
const multer  = require("multer");
const path    = require("path");
const fs      = require("fs");
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
        cb(null, `${emp}-${Date.now()}-${safeName}`);
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

module.exports = router;
