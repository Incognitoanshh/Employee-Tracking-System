const express = require("express");
const multer = require("multer");

const router = express.Router();

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
    upload.single("screenshot"),
    screenshotController.uploadScreenshot
);
router.get(
    "/all",
    screenshotController.getScreenshots
);

module.exports = router;
