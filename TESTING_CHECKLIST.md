# Manual Testing Checklist

Run this before handing the system to the company.

**Build:** `e4c2454` — [CI](https://github.com/Incognitoanshh/Employee-Tracking-System/actions)
**Server:** deployed and verified on `65.21.212.85`

You need three logins to cover everything:

| Role | Sees | Screenshots taken? |
|---|---|---|
| Super admin | Everything, can manage admins | **No** |
| Admin | Employees only, cannot touch admins | Yes |
| Employee | Own panel only | Yes |

---

## What is already verified automatically

Do not re-test these by hand unless something looks wrong — they run in CI
and were checked against the live server.

- All 29 API endpoints exist; 26 protected ones reject requests without a
  token, 4 public ones respond correctly
- SQL injection in the username field, 5000-character input, wrong types,
  nulls, malformed JSON, empty body — all handled, no internal errors leaked
- All 6 admin tabs and all 6 employee pages build and render
- Every button in both panels is wired; sidebar, header and quick-action
  navigation verified by real clicks
- Scheduler produces an identical plan in IST, UTC, Pacific and London —
  50 scenarios, byte-identical
- Daily budget: 1, 5, 10 and 20 per day all produce exactly that number;
  cap holds across 3 days (30 captures from 432 attempts) and survives a
  restart
- Role guards: employee and admin get screenshots and idle tracking,
  super admin gets neither
- Backup and restore verified against production data

---

## 1. Login and session

- [ ] Correct credentials log in
- [ ] Wrong password shows an error, does not crash
- [ ] Empty username or password is rejected with a message
- [ ] Login is not frozen while signing in — the window stays responsive
- [ ] Employee lands on the employee panel, admin on the Control Center
- [ ] Ten wrong passwords in a row lock **that account** for 15 minutes,
      while a different account can still log in from the same machine
- [ ] Logout returns to the login screen with no crash

## 2. Employee panel

**Dashboard**
- [ ] Name, employee ID and role are correct
- [ ] Session Duration counts up from login (not stuck at 00:00:00)
- [ ] Activity Status flips to IDLE after the configured threshold with no
      input, and back to WORKING on a keypress
- [ ] Internet Status shows CONNECTED with a latency figure
- [ ] Screenshots Today increases when a capture happens
- [ ] Recent Activity lists real events with sensible timestamps

**Other pages**
- [ ] Attendance shows this employee's own login/logout rows
- [ ] Activity Logs loads and pages
- [ ] Screenshots lists this employee's own captures
- [ ] Settings shows the values the admin actually set (not defaults)
- [ ] Help page opens

**Tray**
- [ ] Closing the window hides to tray instead of quitting
- [ ] Tray icon reopens the window
- [ ] Tray → Exit actually quits

## 3. Admin panel (log in as admin, not super admin)

**Dashboard**
- [ ] Today's Summary figures look right
- [ ] **Your Session** section shows your own Session Duration, Activity
      Status and Screenshots Today — admins are tracked too
- [ ] Server Status / Database / Tracking / Sync Health all green
- [ ] Last 7 Days charts render
- [ ] Quick action buttons jump to the right page

**Configuration**
- [ ] Selecting an employee shows the scope banner naming them
- [ ] An employee with no override shows "values come from the Global Default"
- [ ] Changing **Screenshots per day** and saving affects **only** that
      employee — check a second employee is untouched
- [ ] Shift start/end save and reappear after Refresh
- [ ] Invalid time (`25:00`, `abc`) is rejected before saving
- [ ] Global Default banner appears when "Global Default" is selected

**Employees**
- [ ] List loads with ID, username, role, status, last seen
- [ ] Search by ID, username and role all filter
- [ ] Add Employee creates an account that can then log in
- [ ] View opens the details dialog with activity and totals
- [ ] Force logout on a logged-in employee ends their session
- [ ] Delete removes an employee
- [ ] Role counts read "n/3 super admins", "n/20 admins"

**Attendance / Screenshots / Audit Logs**
- [ ] Each loads without clicking Refresh
- [ ] Employee ID and date filters work; Clear resets them
- [ ] Prev/Next paginate and the total is right
- [ ] Timestamps are all one format — no raw `2026-08-04 06:48:13.34181`
- [ ] Export CSV downloads and opens, with IST times
- [ ] Double-clicking a screenshot opens the preview and it decrypts
- [ ] Closing the preview mid-load does not crash the app

## 4. Roles

- [ ] Admin **cannot** create another admin (super admin only)
- [ ] Admin **cannot** delete or modify another admin
- [ ] Super admin **can** create and delete admins
- [ ] Super admin cannot delete their own account
- [ ] Super admin gets **no** screenshots and **no** idle tracking
- [ ] Admin and employee both **do** get screenshots

## 5. Screenshots and scheduling

This is the part with the most history behind it — test it properly.

- [ ] Set Screenshots per day to 20 to see captures quickly, then set it
      back to your real value
- [ ] Count for the day never exceeds the configured number
- [ ] Captures are spread across the day, not bunched
- [ ] **Timezone test:** leave the Windows machine on its own timezone and
      the Mac on IST. After a full day both should show the **same count**.
      This is the one that matters most.
- [ ] **Overnight shift:** set 22:00–06:00 for one employee, have them
      logged in at 23:00 and again at 02:00 — captures should happen in both
- [ ] **Off-shift:** leave someone logged in past shift end. Captures should
      stop once the day's budget is spent, not continue every few minutes

## 6. Offline and recovery

- [ ] Disconnect the network for 5 minutes with the app running — it should
      not crash
- [ ] Reconnect — queued screenshots and logs upload
- [ ] Force-quit the app mid-session and reopen — it recovers
- [ ] Reboot the machine — the app starts by itself

## 7. Operations (on the VPS)

- [ ] `bash server/scripts/backup.sh` — reports a db snapshot **and**
      `upload snapshots : 1` (not 0)
- [ ] `bash server/scripts/restore_test.sh` — RESTORE TEST PASSED
- [ ] `crontab -l` shows all four ETS jobs
- [ ] `tail ~/ets-health.log` has recent entries and no ALERT lines

---

## Known limitations — tell the company about these

These are not bugs to find; they are current facts about the system.

1. **No HTTPS.** Passwords and session tokens cross the network in clear
   text. Needs a domain name before rollout. Everything else is ready for it.
2. **Windows Defender quarantines the app.** It is unsigned, so every
   personal machine will need manual intervention until a code-signing
   certificate is bought. This blocks a smooth rollout.
3. **Backups are on the same disk as the data.** Protects against mistakes,
   not against losing the VPS. The off-site rclone job is written but not yet
   configured.
4. **Health check alerts go to a log file**, not to anyone's phone or inbox.
   Someone has to look.
5. **Autostart can be removed by the employee** on Windows (it lives in
   their own registry hive). A Windows Service would be needed to prevent it.
6. **The server is in Finland.** ~300 ms round trip from India, and packet
   loss on that route has been observed causing aborted uploads.
7. **Overnight shifts get the daily budget twice** — once before midnight
   and once after — because the budget is per IST calendar day, as specified.

---

## If something fails

Client log: `~/Library/Application Support/ETS/storage/app.log` (macOS)
or `%LOCALAPPDATA%\ETS\storage\app.log` (Windows).

Server log: `ssh etsadmin@65.21.212.85 'pm2 logs ets-server --lines 50 --nostream'`

For screenshot problems, turn on **Verbose logging** for that employee in
Configuration first — it records the schedule and every capture decision.
Turn it back off afterwards; it fills the database quickly.
