# Amaze ETS — Employee Tracking System

Desktop time and activity tracking for a small company. A Python client runs
on each employee's machine and reports to a Node server.

- **Server** — Node.js + Express + PostgreSQL, on a VPS
- **Client** — Python + PySide6, installed on Windows and macOS
- **Time zone** — everything is IST. The client derives it from UTC rather
  than trusting the machine clock, because a client in another timezone once
  produced 130 screenshots where 10 were configured.

---

## What it does

**Screenshots** — a configurable number per IST calendar day, spread randomly
across the employee's shift and never exceeding the number set. Captured and
AES-256-GCM encrypted on the device; the server only ever holds ciphertext.
Nothing is captured outside the shift, on a weekly off, or on a holiday.

**Attendance** — login and logout times, hours worked, and whether each
arrival was on time or late against that employee's shift.

**Activity** — idle detection with a configurable threshold, and a daily idle
total accumulated as time passes.

**Reports** — per employee over any range up to a year: working days, present,
absent (with the dates), late days, hours, average hours, idle and screenshot
counts. Exports to CSV.

**Administration** — three roles. A super admin manages admins and is not
tracked; admins manage employees; employees see only themselves. Per-employee
configuration falls back to a global default. Passwords can be changed by
their owner and reset by an admin.

---

## Repository layout

```
server/
  server.js              entry point
  config/db.js           PostgreSQL pool, pinned to UTC
  middleware/            JWT verification, role guards
  routes/  controllers/  the API
  utils/
    ist_sql.js           IST calendar-day SQL, in one place
    attendance_status.js on time / late / day off, in one place
    password_policy.js   password rules, in one place
  migrations/            idempotent, safe to re-run
  scripts/               backup, restore test, HTTPS, off-site, verification
  tests/                 run against a real database over HTTP

client/
  main.py                entry point
  core/
    time_ist.py          the single time authority
    work_calendar.py     weekly offs and holidays
  application/           scheduling, sync, session, idle
  presentation/          PySide6 windows
  security/              encryption

tests/                   client-side suites, including 60 timezone scenarios
ets.sql                  complete schema — fresh installs and upgrades
```

Three helpers exist because the same rule living in two places is what caused
this project's worst bugs: a timezone rule that disagreed with itself produced
130 captures where 10 were configured, and later a whole session with none.

---

## Server setup

```bash
# 1. Database
sudo -u postgres createdb ets_db
psql -U postgres -d ets_db -f ets.sql
```

`ets.sql` is both the fresh-install schema and the upgrade path — it renames
and adds columns where needed, so running it on an existing database is safe.

```bash
# 2. Configuration
cd server
cp .env.example .env
```

Fill in `DB_*`, a strong random `JWT_SECRET`, `ENCRYPTION_KEY` and `PORT`
(the deployment uses **8000**). Set `TRUST_PROXY=1` only once nginx is in
front — without nginx it would let a client spoof its own IP.

```bash
# 3. Run
npm install
npm install -g pm2
pm2 start server.js --name ets-server && pm2 save && pm2 startup
```

```bash
# 4. First account
cd server && node scripts/change_credentials.js EMP001
```

The same script recovers a forgotten super-admin password later.

---

## Client setup

Employees install a build; only development needs this:

```bash
pip install -r requirements.txt
cp client/.env.example client/.env   # set API_BASE_URL
python -m client.main
```

Builds come from GitHub Actions, never from a local machine:

```bash
gh workflow run "Build Cross-Platform Desktop Apps" --ref main
gh run download $(gh run list --limit 1 --json databaseId -q '.[0].databaseId') --dir ~/Downloads/ets-build
```

**macOS** needs Screen Recording granted per machine (System Settings →
Privacy & Security), and again after installing a new build — the permission
is tied to the app bundle.

**Windows** quarantines the app because it is unsigned. See DEPLOYMENT.md.

---

## Tests

```bash
python3 tests/run_all.py                      # client
node server/tests/test_password.js            # and the other four
```

The server suites start the real Express app against a scratch PostgreSQL
database and talk to it over HTTP, so route order, middleware, rate limiters
and role guards are all exercised as production has them.

Covered: 60 timezone scenarios across four zones, the daily screenshot cap,
shift-window boundaries including overnight shifts, weekly offs and holidays,
password policy and token rotation, the role hierarchy, late classification,
and report totals including absences and mid-range joiners.

---

## Operations

```bash
bash server/scripts/backup.sh          # nightly dump + uploads, 14-day retention
bash server/scripts/restore_test.sh    # an untested backup is not a backup
bash server/scripts/verify_day.sh      # configured vs captured, per employee
bash server/scripts/enable_https.sh    # needs a domain
bash server/scripts/setup_offsite.sh   # needs a storage account
bash server/scripts/install_cron.sh    # schedules the above
```

---

## Known limitations

1. **No HTTPS.** Passwords and session tokens cross the network in clear text.
   Needs a domain name; `enable_https.sh` does the rest.
2. **The Windows app is unsigned.** Defender quarantines it on every machine
   until a code-signing certificate is bought.
3. **Backups sit on the same disk as the data.** `setup_offsite.sh` fixes this
   once a storage account exists.
4. **Health alerts go to a log file**, not to anyone's phone.
5. **Autostart can be removed by the employee** on Windows.
6. **Overnight shifts get the daily budget twice**, once either side of
   midnight, because the budget is per IST calendar day.
7. **Idle time is only counted from the build that introduced it onward.**

---

## Documentation

| | |
|---|---|
| [DEPLOYMENT.md](DEPLOYMENT.md) | Deploying, HTTPS, backups, monitoring, rollout, rollback |
| [TESTING_CHECKLIST.md](TESTING_CHECKLIST.md) | What to test by hand before handover |
| [BACKUP.md](BACKUP.md) | Backup and restore procedures |
| [FINAL_RELEASE_REPORT.md](FINAL_RELEASE_REPORT.md) | Readiness assessment, blockers, risks |
