-- What has already been emailed, so nothing is sent twice.
--
-- The alerts themselves are DERIVED, not stored: utils/alert_rules.js works
-- them out fresh from attendance, activity and the calendar every time the
-- Alerts page is opened. That is the right design for a page — it can never
-- show a stale alert — but it means an emailer has no memory of its own.
--
-- Without one, "somebody has not logged in" is true every five minutes for
-- the rest of the morning, and the owner gets it every five minutes. Two days
-- of that and the mail is filtered into a folder nobody opens, which is worse
-- than never having sent it: the one morning it matters, it is unread with
-- forty others.
--
-- THE KEY IS (employee_id, alert_type, ist_day).
--
-- One email per person per kind of problem per day. A person who has not
-- logged in is one email at 09:40, not eighty by lunchtime. The day is the
-- IST day, because that is the day everything else in this product counts by
-- — a night shift crossing midnight is handled by utils/ist_sql, not here.
--
-- It resets on its own: tomorrow is a different day, so tomorrow's alert is
-- sent. Nothing has to expire anything.
--
-- WHY THE FAILURES ARE KEPT TOO. A row is written when the send is ATTEMPTED,
-- not when it succeeds, and carries what happened. That way:
--   * a send that failed can be retried, and the retry counted;
--   * an owner who says "I never got the alert" can be shown whether it was
--     sent, refused by the mail server, or never generated at all;
--   * a mailbox that has stopped accepting mail shows up as a column of
--     failures rather than as silence.

CREATE TABLE IF NOT EXISTS alert_emails (
    id            BIGSERIAL PRIMARY KEY,
    employee_id   VARCHAR(50),
    alert_type    VARCHAR(40) NOT NULL,
    ist_day       DATE        NOT NULL,
    severity      VARCHAR(10),
    subject       TEXT,
    recipients    TEXT,
    status        VARCHAR(12) NOT NULL DEFAULT 'sent',   -- sent | failed
    attempts      INTEGER     NOT NULL DEFAULT 1,
    error         TEXT,
    sent_at       TIMESTAMP   NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);

-- One row per person per alert type per day. A retry updates the row it
-- already has rather than adding another, so `attempts` is a count and not a
-- pile of duplicates.
--
-- employee_id is nullable — a digest is about everybody — and NULL is not
-- equal to itself in a UNIQUE index, which would let digests duplicate. The
-- expression below folds NULL onto a value that cannot be an employee id, so
-- one digest a day is enforced the same way.
CREATE UNIQUE INDEX IF NOT EXISTS alert_emails_once_per_day
    ON alert_emails (COALESCE(employee_id, '~digest~'), alert_type, ist_day);

-- The Alerts page shows the most recent deliveries; nothing reads further
-- back than that except somebody investigating.
CREATE INDEX IF NOT EXISTS alert_emails_recent ON alert_emails (sent_at DESC);

-- Who the alerts go to, and how. Empty means nobody, and the sender says so
-- rather than pretending to work.
INSERT INTO app_settings (key, value) VALUES
    ('alert_email_to',        ''),
    -- Immediate mail is for the things that mean somebody is not working
    -- right now. Everything else waits for the daily summary — see
    -- utils/alert_mailer.js for why that split is where it is.
    ('alert_email_immediate', 'NOT_REPORTING,NEVER_REPORTED,NO_LOGIN'),
    ('alert_email_digest',    'true'),
    -- IST, and the hour the working day is far enough along to be worth
    -- summarising.
    ('alert_email_digest_hour', '19')
ON CONFLICT (key) DO NOTHING;
