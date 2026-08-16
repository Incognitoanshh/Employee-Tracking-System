-- Renaming an employee id must carry every row that names them.
--
-- WHY THIS IS NEEDED. employee_id is the primary key and it appears in
-- twenty-eight columns across twenty tables. Every foreign key pointing at it
-- was ON UPDATE NO ACTION, which means a rename does not cascade — it is
-- simply refused, and the id can never be corrected once issued.
--
-- That was fine while ids were permanent by accident. It stops being fine the
-- moment there is a format worth moving to (26AMZEM001 — year, company, role,
-- number), and it would have been a problem the first time somebody was
-- entered with a typo in their id, which cannot be fixed today at all.
--
-- ON UPDATE CASCADE, ON DELETE UNCHANGED. Each constraint keeps exactly the
-- delete rule it already had — the two are independent, and quietly turning a
-- SET NULL into a CASCADE would start deleting messages when an account goes.
-- The list below is generated from the live schema for that reason rather
-- than typed out.
--
-- WHAT THIS DOES NOT COVER: the columns that hold an employee id WITHOUT a
-- foreign key — attendance, activity_logs, screenshots, idle_daily,
-- employee_configs, active_sessions, employees.suspended_by,
-- holidays.created_by, and the messages.sender_employee_code snapshot. Those
-- are updated explicitly by scripts/renumber_employee_ids.js, which is the
-- only thing that should ever rename an id.

ALTER TABLE attachments DROP CONSTRAINT attachments_uploaded_by_fkey;
ALTER TABLE attachments ADD CONSTRAINT attachments_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE SET NULL;
ALTER TABLE channel_members DROP CONSTRAINT channel_members_employee_id_fkey;
ALTER TABLE channel_members ADD CONSTRAINT channel_members_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE channels DROP CONSTRAINT channels_created_by_fkey;
ALTER TABLE channels ADD CONSTRAINT channels_created_by_fkey FOREIGN KEY (created_by) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE SET NULL;
ALTER TABLE chat_access_log DROP CONSTRAINT chat_access_log_viewer_id_fkey;
ALTER TABLE chat_access_log ADD CONSTRAINT chat_access_log_viewer_id_fkey FOREIGN KEY (viewer_id) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE SET NULL;
ALTER TABLE email_verifications DROP CONSTRAINT email_verifications_employee_id_fkey;
ALTER TABLE email_verifications ADD CONSTRAINT email_verifications_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE employees DROP CONSTRAINT fk_employee_manager;
ALTER TABLE employees ADD CONSTRAINT fk_employee_manager FOREIGN KEY (reporting_manager) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE SET NULL;
ALTER TABLE mentions DROP CONSTRAINT mentions_employee_id_fkey;
ALTER TABLE mentions ADD CONSTRAINT mentions_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE message_edits DROP CONSTRAINT message_edits_edited_by_fkey;
ALTER TABLE message_edits ADD CONSTRAINT message_edits_edited_by_fkey FOREIGN KEY (edited_by) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE SET NULL;
ALTER TABLE message_reads DROP CONSTRAINT message_reads_employee_id_fkey;
ALTER TABLE message_reads ADD CONSTRAINT message_reads_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE messages DROP CONSTRAINT messages_deleted_by_fkey;
ALTER TABLE messages ADD CONSTRAINT messages_deleted_by_fkey FOREIGN KEY (deleted_by) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE SET NULL;
ALTER TABLE messages DROP CONSTRAINT messages_sender_id_fkey;
ALTER TABLE messages ADD CONSTRAINT messages_sender_id_fkey FOREIGN KEY (sender_id) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE SET NULL;
ALTER TABLE messages DROP CONSTRAINT messages_pinned_by_fkey;
ALTER TABLE messages ADD CONSTRAINT messages_pinned_by_fkey FOREIGN KEY (pinned_by) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE SET NULL;
ALTER TABLE notifications DROP CONSTRAINT notifications_employee_id_fkey;
ALTER TABLE notifications ADD CONSTRAINT notifications_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE team_members DROP CONSTRAINT team_members_employee_id_fkey;
ALTER TABLE team_members ADD CONSTRAINT team_members_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE CASCADE;
ALTER TABLE teams DROP CONSTRAINT teams_archived_by_fkey;
ALTER TABLE teams ADD CONSTRAINT teams_archived_by_fkey FOREIGN KEY (archived_by) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE SET NULL;
ALTER TABLE teams DROP CONSTRAINT teams_created_by_fkey;
ALTER TABLE teams ADD CONSTRAINT teams_created_by_fkey FOREIGN KEY (created_by) REFERENCES employees(employee_id) ON UPDATE CASCADE ON DELETE SET NULL;
