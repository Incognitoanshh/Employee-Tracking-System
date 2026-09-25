-- A screenshot an administrator asked for, rather than one the schedule took.
--
-- The client cannot be reached from here: it polls. So a request is written
-- down, the employee's next config sync carries it, and the upload that comes
-- back names the request it answers. A row that is never answered — the app
-- was closed between the click and the poll — expires on its own rather than
-- waiting forever for a capture that is not coming.
--
-- requested_by is kept because an administrator looking at somebody's screen
-- on demand is an act somebody may later have to answer for. The audit log
-- carries it too; this is the copy that stays attached to the picture.
CREATE TABLE IF NOT EXISTS screenshot_requests (
    id            BIGSERIAL PRIMARY KEY,
    employee_id   VARCHAR(50)  NOT NULL,
    requested_by  VARCHAR(50)  NOT NULL,
    requested_at  TIMESTAMP    NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    delivered_at  TIMESTAMP,
    taken_at      TIMESTAMP,
    screenshot_id INTEGER,
    status        VARCHAR(16)  NOT NULL DEFAULT 'PENDING'
);

-- The sync endpoint asks this question every five seconds, for every employee
-- who is signed in: "is anything pending for me".
CREATE INDEX IF NOT EXISTS idx_screenshot_requests_pending
    ON screenshot_requests (employee_id, status);
