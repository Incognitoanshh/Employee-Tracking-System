ALTER TABLE screenshots 
ADD COLUMN IF NOT EXISTS session_id INTEGER,
ADD COLUMN IF NOT EXISTS upload_status TEXT DEFAULT 'PENDING',
ADD COLUMN IF NOT EXISTS upload_attempts INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS last_upload_attempt TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_screenshots_status ON screenshots(upload_status);
