# Manual Testing Checklist

Work through this before handing the system over. It covers only what
automated tests cannot reach — real screen capture, OS permissions, visual
layout, live sync and timing.

**Server:** `65.21.212.85`, verified deployed
**Builds:** GitHub Actions → latest run on `main` → artifacts

```bash
gh run download $(gh run list --branch main --limit 1 --json databaseId -q '.[0].databaseId') --dir ~/Downloads/ets-build
```

You need three logins to cover everything:

| Role | Sees | Screenshots + idle tracked? |
|---|---|---|
| Super admin | Everything, can manage admins | **No** |
| Admin | Employees only, cannot touch other admins | Yes |
| Employee | Own panel only | Yes |

---

## Already verified automatically — do not re-test by hand

These run in CI or against a real database. Re-test only if something looks
wrong.

- Every endpoint requires a token; nothing outside `/api/auth` is unguarded
- SQL injection, 5000-character input, wrong types, nulls, malformed JSON and
  empty bodies all handled without leaking internal errors
- Scheduler produces an identical plan in IST, UTC, Pacific and London —
  60 scenarios, byte-identical
- Daily budget: 1, 5, 10 and 20 per day each produce exactly that number; the
  cap holds across 3 days and survives a restart
- Captures fall **inside the shift only** — verified for 09:00–18:00,
  10:00–19:00, 11:00–20:00 and the 22:00–06:00 overnight case
- Weekly offs and holidays produce zero captures; an overnight shift belongs
  to the day it starts
- Password change and reset: policy, token rotation, cross-device sign-out,
  the temporary-password round trip, and the role hierarchy
- Usernames match without regard to case, and a case-variant account cannot
  be created
- Late / on-time / day-off classification, including overnight shifts
- Report totals: absences, weekly offs, holidays, mid-range joiners, idle
- Backup and restore, against production data

---

## 1. First launch on a new machine

- [ ] **macOS only:** the first capture fails until Screen Recording is
      granted. System Settings → Privacy & Security → **Screen & System Audio
      Recording** → enable **Amaze ETS**, then quit from the tray (not just
      the window) and reopen
- [ ] **Windows only:** Defender quarantines the app. It is unsigned — see
      DEPLOYMENT.md for what to tell employees
- [ ] Granting permission and reopening produces captures at the scheduled
      times
- [ ] Installing a newer build asks for Screen Recording again — expected, the
      permission is tied to the app bundle

## 2. Login and session

- [ ] Correct credentials log in
- [ ] Username works in any case — `Amazeinternet`, `amazeinternet`,
      `AMAZEINTERNET`
- [ ] Wrong password shows an error and does not crash
- [ ] Empty username or password is rejected with a message
- [ ] The window stays responsive while signing in
- [ ] Employee lands on the employee panel, admin on the Control Center
- [ ] Ten wrong passwords lock **that account** for 15 minutes while a
      different account still logs in from the same machine
- [ ] Logout returns to the login screen without crashing

## 3. Passwords

- [ ] Employee panel → Settings → Security → **Change Password**
- [ ] Wrong current password is refused
- [ ] A new password under 8 characters is refused
- [ ] Reusing the current password is refused
- [ ] A valid change succeeds **and leaves you signed in**
- [ ] A second device signed in as the same person is signed out
- [ ] Closing and reopening the app still auto-logs-in — the stored token was
      refreshed too
- [ ] Admin panel sidebar → **Change Password** works the same way
- [ ] Employees → Manage → **Reset password** shows a temporary password
      **once**; copy it before closing
- [ ] That temporary password signs in and forces a change screen that
      **cannot be cancelled**
- [ ] After choosing their own password, the forced screen stops appearing
- [ ] An admin cannot reset another admin's password (greyed out)

## 4. Employee panel

- [ ] Name, employee ID and role are correct
- [ ] Session Duration counts up from login, not stuck at 00:00:00
- [ ] Activity Status flips to IDLE after the configured threshold and back to
      WORKING on a keypress
- [ ] Internet Status shows CONNECTED with a latency figure
- [ ] Screenshots Today increases when a capture happens
- [ ] Recent Activity lists real events with sensible timestamps
- [ ] Attendance, Activity Logs and Screenshots each show only this employee
- [ ] Settings shows the values the admin actually set, not defaults
- [ ] Closing the window hides to tray; the tray icon reopens it; Exit quits

## 5. Admin panel

**Dashboard**
- [ ] Today's Summary looks right
- [ ] **Your Session** shows your own duration, status and screenshot count —
      admins are tracked too
- [ ] Server / Database / Tracking / Sync Health all green
- [ ] Last 7 Days charts render; quick actions jump to the right page

**Configuration**
- [ ] Selecting an employee shows a banner naming them
- [ ] An employee with no override says the values come from the Global Default
- [ ] Changing **Screenshots per day** affects **only** that employee — check a
      second employee is untouched
- [ ] Shift start/end save and survive Refresh
- [ ] Invalid time (`25:00`, `abc`) is refused before saving
- [ ] **Weekly off** shows seven readable day names
- [ ] Ticking a day and saving takes effect **without logging out**
- [ ] Ticking all seven days is refused
- [ ] **Late after** saves, and changing it reclassifies past rows on the
      Attendance page immediately
- [ ] **Holidays** card says "applies to everyone"; adding today's date stops
      captures within about 10 seconds; removing it starts them again

**Employees**
- [ ] List loads with ID, username, role, status, last seen
- [ ] Search by ID, username and role filters
- [ ] Add Employee creates an account that can then log in
- [ ] A username differing only by case is refused
- [ ] A weak password is refused
- [ ] View opens the details dialog; Force logout ends a session; Delete removes
- [ ] Role counts read "n/3 super admins", "n/20 admins"

**Attendance / Screenshots / Audit Logs**
- [ ] Each loads without clicking Refresh
- [ ] Filters work; Clear resets them; Prev/Next paginate with a correct total
- [ ] Timestamps are one consistent format — no raw `2026-08-04 06:48:13.34181`
- [ ] Attendance **Status** column reads On time / Late 45m / Day off /
      Outside shift, and a second login the same day shows a dash
- [ ] Export CSV downloads, opens, shows IST times and includes Status
- [ ] Double-clicking a screenshot opens the preview and it decrypts
- [ ] Closing the preview mid-load does not crash the app

**Reports**
- [ ] Pick a range and Generate
- [ ] Weekly offs and holidays are **not** counted as absences
- [ ] Hovering the Absent count lists the dates
- [ ] Someone who joined mid-range is not absent for days before they joined
- [ ] **Idle** shows a dash until clients have reported, and turns amber while
      only some days have reported
- [ ] Export CSV includes absent dates and idle hours

## 6. Roles

- [ ] Admin cannot create, delete or modify another admin
- [ ] Super admin can create and delete admins, but not delete themselves
- [ ] Super admin gets **no** screenshots and **no** idle tracking
- [ ] Admin and employee both do get screenshots

## 7. Scheduling — the part with the most history

- [ ] Set Screenshots per day to 20 to see captures quickly, then set it back
- [ ] The count for a day never exceeds the configured number
- [ ] Captures are spread across the shift, not bunched
- [ ] **Nothing is captured before the shift starts or after it ends**
- [ ] **Timezone test — the one that matters most.** Leave the Windows machine
      on its own timezone and the Mac on IST. After a full day both must show
      the **same count**. This is the only item on this list that has broken
      production twice; do not skip it
- [ ] **Overnight shift:** set 22:00–06:00, be logged in at 23:00 and again at
      02:00 — captures in both
- [ ] **Weekend:** set Sunday as the weekly off and leave someone logged in
      through it — no captures

## 8. Offline and recovery

- [ ] Disconnect the network for 5 minutes with the app running — no crash
- [ ] Reconnect — queued screenshots, logs and idle totals upload
- [ ] Force-quit mid-session and reopen — it recovers
- [ ] Reboot — the app starts by itself

## 9. Operations (on the VPS)

- [ ] `bash server/scripts/backup.sh` reports a db snapshot **and**
      `upload snapshots : 1`
- [ ] `bash server/scripts/restore_test.sh` reports RESTORE TEST PASSED
- [ ] `crontab -l` shows the ETS jobs
- [ ] `tail ~/ets-health.log` has recent entries and no ALERT lines

---

## Known limitations — tell the company

Facts about the system as delivered, not bugs to find.

1. **No HTTPS.** Passwords and session tokens cross the network in clear text.
   Needs a domain name; everything else is ready. See DEPLOYMENT.md.
2. **The Windows app is unsigned.** Defender will quarantine it on every
   personal machine until a code-signing certificate is bought.
3. **macOS needs Screen Recording granted per machine**, and again after
   installing a new build.
4. **Backups are on the same disk as the data.** Protects against mistakes,
   not against losing the VPS. The off-site job is written but not configured.
5. **Health check alerts go to a log file**, not to anyone's phone or inbox.
6. **Autostart can be removed by the employee** on Windows — it lives in their
   own registry hive. A Windows Service would be needed to prevent that.
7. **The server is in Finland.** ~300 ms from India, and packet loss on that
   route has been observed causing aborted uploads.
8. **Overnight shifts get the daily budget twice** — once before midnight and
   once after — because the budget is per IST calendar day.
9. **Idle time is only counted from this build onward.** Days before it was
   installed report nothing, which the report shows as a dash rather than zero.

---

## If something fails

Client log: `~/Library/Application Support/ETS/storage/app.log` (macOS) or
`%LOCALAPPDATA%\ETS\storage\app.log` (Windows).

Server log:
```bash
ssh etsadmin@65.21.212.85 'pm2 logs ets-server --lines 50 --nostream'
```

For screenshot problems, turn on **Verbose logging** for that employee in
Configuration first — it records the schedule and every capture decision. Turn
it back off afterwards; it fills the database quickly.
