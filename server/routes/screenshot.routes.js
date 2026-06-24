const express = require("express");
const multer = require("multer");

const router = express.Router();
const { verifyToken } = require(
    "../middleware/auth.middleware"
);

const screenshotController = require(
    "../controllers/screenshot.controller"
);

const storage = multer.diskStorage({

    destination: (req, file, cb) => {
        cb(null, "uploads/screenshots");
    },

    filename: (req, file, cb) => {
        cb(
            null,
            Date.now() + "-" + file.originalname
        );
    }

});

const upload = multer({ storage });

router.post(
    "/upload",
    verifyToken,
    upload.single("screenshot"),
    screenshotController.uploadScreenshot
);
router.get(
    "/all",
    verifyToken,
    screenshotController.getScreenshots
);
router.get(
    "/download/:id",
    verifyToken,
    screenshotController.downloadScreenshot
);

module.exports = router;
