const express = require("express");
const multer  = require("multer");
const path    = require("path");
const fs      = require("fs");
const router  = express.Router();

const screenshotController = require("../controllers/screenshot.controller");

// BUG FIX: verifyToken yahan se remove kiya — server.js mein already laga hai
// app.use("/api/screenshots", verifyToken, screenshotRoutes)
// Double middleware se req.employee undefined ho sakta tha in some edge cases.

// uploads/screenshots directory ensure karo
const uploadDir = path.join(__dirname, "../uploads/screenshots");
if (!fs.existsSync(uploadDir)) {
    fs.mkdirSync(uploadDir, { recursive: true });
}

const storage = multer.diskStorage({
    destination: (req, file, cb) => {
        cb(null, uploadDir);
    },
    filename: (req, file, cb) => {
        // employee_id + timestamp — collision avoid karo
        const emp = req.employee?.employee_id || "unknown";
        cb(null, `${emp}-${Date.now()}-${file.originalname}`);
    }
});

const upload = multer({
    storage,
    limits: { fileSize: 10 * 1024 * 1024 }, // 10MB max
    fileFilter: (req, file, cb) => {
        // Sirf images allow karo
        if (file.mimetype.startsWith("image/")) {
            cb(null, true);
        } else {
            cb(new Error("Only image files allowed"), false);
        }
    }
});

router.post("/upload",         upload.single("screenshot"), screenshotController.uploadScreenshot);
router.get("/all",             screenshotController.getScreenshots);
router.get("/download/:id",    screenshotController.downloadScreenshot);

module.exports = router;
