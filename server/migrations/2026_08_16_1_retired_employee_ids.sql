-- An employee id is never given to a second person.
--
-- It is already unique among people who exist — it is the primary key. What
-- it was not is unique over TIME: deleting somebody freed their id, and the
-- next person created could be handed it.
--
-- WHY THAT MATTERS EVEN THOUGH DELETION IS THOROUGH. Deleting an account
-- removes its attendance, screenshots, activity logs and configuration, so a
-- recycled id does not merge two people's tracking. But the record does not
-- end at this database:
--
--   * every CSV an administrator has already exported names people by this
--     id, and those files outlive the row;
--   * chat messages keep `sender_employee_code` as a snapshot, so an old
--     conversation goes on showing EMP009 beside the name of whoever sent it,
--     while EMP009 now means somebody else entirely;
--   * it is what people type into a search box and read off a report, which
--     is exactly the owner's own description of it — a roll number.
--
-- A roll number is not reissued when a student leaves, and for the same
-- reason: the paperwork already left the building.
--
-- THE ROW OUTLIVES THE ACCOUNT ON PURPOSE, so there is no foreign key here.
-- A reference to employees would be deleted along with the very account whose
-- id this exists to remember.

CREATE TABLE IF NOT EXISTS retired_employee_ids (
    employee_id  VARCHAR(50) PRIMARY KEY,
    full_name    TEXT,
    retired_at   TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    retired_by   VARCHAR(50)
);
