# Amaze ETS — Final Release Report

**Date:** 2026-08-06
**Version:** 2.1.0
**Audited commit:** `7acefdf`
**Verdict:** **CODE COMPLETE — NOT YET SAFE TO DEPLOY WIDELY.** Every blocker
left is a purchase or a decision, not a defect. See Blockers.

### What changed since the 2026-08-04 audit

Four features were added after that report, along with three pre-existing
bugs found while building them:

| Added | |
|---|---|
| Password change and reset | Nobody could change a password anywhere in the system. A forgotten one meant a new account, losing that person's history with it. |
| Weekly offs and holidays | The scheduler treated Sunday like any other day and captured people through their weekends. |
| Late / on-time on Attendance | Shift times and login times had both been stored for months and were never once compared. |
| Reports with absences and idle | Totals payroll actually asks for. Idle is accumulated by the client rather than inferred from event pairs, which could not be made trustworthy. |

| Bug found | Why it had gone unnoticed |
|---|---|
| Config changes never rescheduled | The check for "did anything change" read values that had already been overwritten, so it always concluded nothing had. Shift changes still worked — they compare against a different source — and those were the edits people made most often. |
| `ets.sql` was on the pre-rename schema | A fresh install created the old column name and every config save returned 500. Nobody had done a fresh install since the rename. |
| Usernames were case-sensitive | The super admin could not sign in by typing their own name in lower case. The error said "Invalid credentials", which reads as a wrong password. |

---

## Production readiness score: 92 / 100

| Area | Score | Notes |
|---|---|---|
| Application code | 97 | No open code-level defects. Verified by hand against production on 2026-08-06. |
| Database | 95 | Schema, indexes, migrations idempotent and order-independent. Pool sized for 1000+. |
| Security — application | 90 | Auth, JWT, SQL injection, upload handling all verified clean. |
| Security — transport | **30** | Plain HTTP. Passwords and JWTs cross the network in clear text. |
| Backups | 95 | Installed on the VPS. Full backup + restore verified against production data. |
| Monitoring | 85 | Cron jobs installed and running. Alerts go to a log file, not pushed. |
| Off-site backup | **40** | Written and now a single command (`setup_offsite.sh`), but not yet configured. Backups still sit on the same disk as the data. |
| Test coverage | 95 | 5 client suites and 5 server suites, the latter driven through the real app against a real database. |
| Windows deployment | **40** | Defender quarantines the binary on every machine. |
| macOS deployment | 85 | Builds and runs. Unsigned, so Gatekeeper warns on first launch. |
| Performance | 90 | Server is not the bottleneck; network RTT is. |
| Reliability | 92 | Crash handlers, graceful shutdown, offline queue, thread safety verified. Scheduling confirmed on two machines 12.5 hours apart; the midnight rollover is still unproven on real hardware. |

The score is capped by three items, none of which is a code defect: HTTPS
needs a domain, Windows deployment needs a certificate, and off-site backup
needs a storage account. Each is now reduced to one command or one purchase.
Everything resolvable inside the repository or on the VPS is done.

A full manual pass was completed on 2026-08-06 and the timezone behaviour is
now supported by evidence from two real machines in two zones. **One code path
remains unproven on real hardware** — the midnight rollover. See below.

---

## Manual verification — 2026-08-06

Everything below was exercised by hand against the production server, on a
real Mac and a real Windows machine. This is the half that automated tests
cannot reach.

| Verified working | |
|---|---|
| Screenshot capture | Both platforms, after granting macOS Screen Recording |
| Idle threshold | Detection and the configured threshold |
| Employee creation | Account created and able to sign in |
| Reports | Generated and read |
| Attendance | Including the on-time / late column |
| Force logout | Ends the target's session |
| Configuration | Shift and idle threshold changes apply **without a logout** |
| UI refresh | Pages update on time, no stale data |
| Verbose logging | Produces the extra scheduler detail it promises |
| Timings | Captures land when the schedule says they will |

The configuration line matters most: applying a change without signing out
is exactly what was broken before this release, and it took a re-login to
see any effect.

### Timezone — most of the way there

Two clients ran the same IST shift on machines 12.5 hours apart — Windows on
Pacific, Mac on IST — with deliberately different daily counts.

| | Windows (Pacific) | Mac (IST) |
|---|---|---|
| Configured per day | 20 | 10 |
| Fraction of its window elapsed | 48% | 33% |
| Expected by then | 9.6 | 3.3 |
| **Actually captured** | **10** | **3** |

Both tracked their own schedule's pace exactly, and every capture on both
machines fell inside 09:00–18:00 **IST** — while one machine's own clock read
the previous evening. A timezone leak would have shown as captures landing in
the local-clock window, or at several times the intended rate, which is
precisely how the 130-versus-10 incident presented. Neither happened.

Neither client ran a complete shift, so the exact end-of-day figure is still
unconfirmed.

## Still unverified: the midnight rollover

One code path remains untested on real hardware. At IST midnight the client
plans the new day, and neither machine was running when that happened.

That is the same path that once produced a whole session with zero captures
and no error anywhere — the failure mode that is invisible until somebody
goes looking for screenshots that were never taken. CI cannot reach it: it
runs one process, on one machine, with a faked clock.

To close it, leave both machines signed in across midnight — a 22:00–06:00
shift is the direct way — and the next day run:

```bash
bash server/scripts/verify_day.sh
```

Both must show their own configured number, with `exact` as the verdict and
no `OVER THE LIMIT` anywhere. Everything for this is already configured; it
needs a night, not a setup.

Until then, treat the rollover as unproven regardless of what CI reports.

---

## Blockers

### 1. No HTTPS — **external blocker** (needs a domain)

The most serious remaining issue.

Screenshots are AES-256-GCM encrypted on the client, so their contents
are safe in transit. **Passwords and JWT tokens are not.** They travel as
plain text over HTTP. Anyone on the office LAN, and any operator along
the India→Finland path, can read them. A stolen super-admin token grants
access to every employee's screenshots.

Certbot cannot issue a certificate for a bare IP address, so this needs a
domain name pointed at `65.21.212.85`.

Once it exists, this is one command:

```bash
bash server/scripts/enable_https.sh ets.yourcompany.com you@company.com
```

It refuses to start if DNS is not pointing here, sets `TRUST_PROXY=1` (without
which nginx makes every request look like `127.0.0.1` and one shared
rate-limit bucket locks out the whole office), and closes port 8000.

**The clients must then be rebuilt.** Each has the API address compiled in, so
closing port 8000 without reinstalling them means nobody can sign in at all.
The script says so at the end rather than leaving it to be remembered.

**After TLS is live**, change `API_BASE_URL` in
`.github/workflows/build.yml` to the `https://` URL and rebuild.
Distributing clients built against the HTTP URL means the certificate
protects nothing.

### 2. Windows Defender quarantines the build — **external blocker**

Reproduced on Windows Server 2022. Defender removes `Amaze ETS.exe`
during download or while running. Documented in detail in `DEPLOYMENT.md`.

`--noupx` reduces the likelihood but does not solve it. The binary is
unsigned, self-extracting, captures the screen, installs itself at
startup, and uploads to a hardcoded IP — collectively that is what
heuristics are built to catch.

Fixes, in order of preference:
1. Code-signing certificate (only option that works on machines outside
   your control; EV grants immediate SmartScreen reputation)
2. Defender exclusion pushed by GPO/Intune (managed machines only, but
   free and path-based so it survives new builds)
3. False-positive submission to Microsoft (per file hash, so per release)

**This needs an answer first:** will every employee machine be
company-managed, or will some be personal laptops? Managed-only means
option 2 is sufficient. Any personal machine means option 1.

### 3. ~~Production operations not installed~~ — **RESOLVED 2026-08-04**

Installed and verified on the production VPS:

```
=== ETS backup  2026-08-04T03:00:40+0000 ===
[2/4] verifying
  OK: 244K, 9 tables
[3/4] rsync uploads -> /home/etsadmin/ets-backups/uploads/20260804
  OK: 55 files, 136M apparent
db snapshots     : 2
upload snapshots : 1
```

```
=== Restore test  2026-08-04T03:01:12+0000 ===
as     : postgres superuser (app user lacks CREATEDB)
  employees          4
  employee_configs   3
  attendance         186
  activity_logs      20007
  screenshots        97
  active_sessions    5

RESTORE TEST PASSED — 4 employees, 1 super admin(s)
```

Two bugs in the backup tooling were found by running it on the VPS —
neither reproduced locally. Both are described under "Fixed in this
audit".

All four cron jobs are installed and running.

---

## Fixed in this audit

### Express `trust proxy` was unset — would have caused an office-wide lockout

Express reads the client address from the socket unless told otherwise.
The moment nginx sits in front, every request arrives from `127.0.0.1`,
so the per-IP limiter in `auth.routes.js` buckets the entire company into
one 300-request/15-minute budget. A thousand employees exhaust that in
seconds and nobody can log in.

This is exactly the office-wide lockout the two-layer limiter was written
to prevent, reintroduced through the proxy. Because HTTPS requires nginx,
it would have fired on the first TLS deploy — a self-inflicted outage
during the security rollout.

Both failure modes were verified:

| Configuration | `req.ip` | Consequence |
|---|---|---|
| No trust proxy, behind nginx | `::1` for everyone | Whole office shares one bucket |
| Trust proxy on, port 8000 exposed | forged header believed | Attacker gets a fresh bucket per request |

So the setting is env-gated (`TRUST_PROXY`), defaults to off, and takes a
hop count rather than `true`. Enable it **at the same time** as putting
nginx in front and firewalling port 8000 to localhost.

### `uploads/` grew without limit, and the documented cleanup destroyed data

`retention_purge.sql` deletes rows from `screenshots` but cannot touch
the `.enc` files. Nothing else did either.

At 1000 employees × 8 captures/day × ~200 KB that is **~1.5 GB/day** —
the 107 GB volume fills in about **70 days**, after which uploads fail
and PostgreSQL cannot write.

Worse, the cleanup procedure documented in that file was:

```
psql ... -c "SELECT file_name FROM screenshots" > /tmp/keep.txt
ls | grep -vxFf /tmp/keep.txt | xargs -r rm --
```

If the psql call fails for any reason — wrong DB name, auth failure,
server down — `keep.txt` is empty. `grep -vxFf` against an empty pattern
file matches nothing, `-v` inverts that to everything, and **every
screenshot on disk is deleted**. Reproduced. With no backups in place
that is unrecoverable.

Replaced with `server/scripts/purge_screenshots.sh`, which refuses to run
when the keep-list is empty but files exist, refuses to delete more than
90% in one pass, and is dry-run by default. Both guards tested.

### Backup retention deleted the snapshot it had just created

Found by running the script on the VPS, not locally.

```
[3/4] rsync uploads -> .../uploads/20260804
  OK: 55 files, 136M apparent
[4/4] pruning older than 14 days
  removed .../uploads/20260804
upload snapshots : 0
```

`rsync -a` preserves the source directory's mtime on the destination.
`server/uploads/` is itself rarely modified — screenshots land in the
`uploads/screenshots/` subdirectory — so its mtime is old. The fresh
snapshot inherited that old mtime, `-mtime +14` matched it, and it was
removed seconds after being written.

The effect: **screenshots were never being backed up at all.** Only the
database dump survived each run. Since the failure is silent and the run
otherwise reports success, this would have been discovered during a
recovery.

Retention now parses the date from the directory name, which this script
controls, and only considers directories matching `YYYYMMDD`. DB dumps
still use mtime, which is correct for plain files nothing rewrites.

### Restore test could not create its throwaway database

`ERROR: permission denied to create database` — the application's
database user has no CREATEDB privilege. That is correct and should stay
that way; granting it so a test can run would widen the app's privileges
for no good reason. The script now falls back to the local `postgres`
superuser, the same route the migrations were applied through.

---

## Verified clean

Checked this pass and found no production-impacting issues:

- **API security** — every route sits behind `verifyToken` at the app
  level (`server.js:85-91`). Tested live: no token → 401; garbage,
  tampered signature, and `alg=none` → 403. The `alg=none` rejection
  matters; it is a classic JWT bypass.
- **SQL injection** — all 16 template interpolations in server SQL were
  inspected individually. Every one is either a server-side constant, a
  numeric placeholder index, or a fragment built from `$n` placeholders
  with values passed separately. No user input reaches SQL as text. No
  f-string SQL on the client.
- **File uploads** — 10 MB multer limit, MIME/extension filter, filename
  sanitised against path traversal.
- **Thread safety** — 6 concurrent SQLite writers plus 4 readers, 1200
  operations: zero lock errors. Real client load is orders of magnitude
  lower.
- **Memory / thread leaks** — 12 build-and-destroy cycles of the heaviest
  tab left 0 live `QThread` objects.
- **Offline sync** — the queue drains at 1200 logs/hour against a normal
  generation rate of ~478/day/employee. A client offline for a month
  catches up in about 12 hours.
- **Process resilience** — `uncaughtException`, `unhandledRejection`,
  SIGTERM/SIGINT handlers with graceful `server.close()`.
- **Performance** — 40 concurrent requests: 40/40 success, median 329 ms,
  p95 396 ms. Min 305 ms vs max 407 ms is a 100 ms spread, so the server
  is idle and **network RTT is the bottleneck**, not the application.
- **Secrets** — no `.env` committed. `.env.example` contains placeholders
  only. The encryption key is injected from a GitHub secret at build time.
- **Builds** — Windows and macOS both green on the latest commit.

---

## Risk assessment

| Risk | Likelihood | Impact | Status |
|---|---|---|---|
| Credential theft over plain HTTP | Medium | **Critical** | Open — blocker 1 |
| VPS loss with no backups | Low | **Critical** | Open — blocker 3 |
| Defender blocks rollout | **Certain** | High | Open — blocker 2 |
| Disk fills, service stops | Was certain by ~day 70 | High | **Fixed** (purge script; needs cron) |
| Office-wide login lockout via nginx | Was certain on TLS deploy | High | **Fixed** (`TRUST_PROXY`) |
| Accidental mass screenshot deletion | Medium | High | **Fixed** (guards in purge script) |
| Backups exist but are unrestorable | Medium | **Critical** | **Mitigated** (verified dump + restore test) |
| India→Finland packet loss | Observed 40% one day | Medium | Accepted — monitor before acting |
| Single VPS, no redundancy | Low | High | Accepted for current scale |

---

## Deployment checklist

### Phase 1 — operations (do first; unblocks everything else)

```bash
# 1. Ship the scripts
cd "/Users/ansh/Downloads/Employee-Tracking-System-main copy" && \
tar czf - server/scripts deploy/nginx-ets.conf server/migrations/retention_purge.sql | \
ssh etsadmin@65.21.212.85 'cd "$HOME/ETS-v5/Employee-Tracking-System-main copy" && tar xzf -'

# 2. Install all cron jobs (idempotent)
ssh -t etsadmin@65.21.212.85 'bash "$HOME/ETS-v5/Employee-Tracking-System-main copy/server/scripts/install_cron.sh"'

# 3. Take the first backup now, do not wait for 02:00
ssh -t etsadmin@65.21.212.85 'bash "$HOME/ETS-v5/Employee-Tracking-System-main copy/server/scripts/backup.sh"'

# 4. Prove it can be restored — a backup nobody has restored is not a backup
ssh -t etsadmin@65.21.212.85 'bash "$HOME/ETS-v5/Employee-Tracking-System-main copy/server/scripts/restore_test.sh"'
```

Expected from step 4: `RESTORE TEST PASSED — N employees, 1 super admin(s)`.

```bash
# 5. Auto-start after reboot
ssh -t etsadmin@65.21.212.85 'pm2 save && pm2 startup'    # run the command it prints
ssh etsadmin@65.21.212.85 'sudo systemctl enable postgresql && systemctl is-enabled postgresql'

# 6. Verify log rotation is active
ssh etsadmin@65.21.212.85 'pm2 install pm2-logrotate 2>/dev/null; pm2 conf pm2-logrotate'
```

### Phase 2 — data reset (before real employees)

```bash
ssh -t etsadmin@65.21.212.85 'bash "$HOME/ETS-v5/Employee-Tracking-System-main copy/server/scripts/reset_test_data.sh"'
```

Then clear local client storage on **every** machine that has run the app,
or unsynced local data will re-upload and undo the reset:

- macOS: `rm -rf ~/Library/Application\ Support/ETS/storage`
- Windows: delete `%LOCALAPPDATA%\ETS\storage`

Then set the global shift:

```bash
./set_global_shift.py <admin-username> '<password>' 09:00 18:00
```

### Phase 3 — HTTPS (needs a domain)

1. Point `ets.yourdomain.com` at `65.21.212.85`
2. Edit `server_name` in `deploy/nginx-ets.conf`, install it, `nginx -t`, reload
3. `sudo certbot --nginx -d ets.yourdomain.com`
4. Set `TRUST_PROXY=1` in `server/.env`, `pm2 restart ets-server`
5. Firewall port 8000 to localhost — **required**, otherwise the forged
   `X-Forwarded-For` bypass described above becomes live
6. Update `API_BASE_URL` in `.github/workflows/build.yml` to the HTTPS URL, rebuild
7. Redistribute clients

### Phase 4 — Windows signing decision

Answer the managed-vs-personal question, then either buy a code-signing
certificate and add a signing step to the workflow, or push a Defender
exclusion via GPO/Intune.

### Phase 5 — staged rollout

1. One real Windows machine, one Mac. Verify: login, attendance,
   screenshot capture, upload, offline mode with the network off, resync
   when it returns, recent activity, force logout, and an **overnight
   shift** (22:00–06:00) — the overnight fix is unit-tested but has not
   been confirmed on real hardware.
2. 5–10 employees for a week. Watch `~/ets-health.log` and
   `~/ets-backup.log` daily.
3. Full rollout.

---

## Rollback procedure

**Application (client or server code)**

```bash
git revert <commit> && git push origin main
# server:
ssh -t etsadmin@65.21.212.85 'cd "$HOME/ETS-v5/Employee-Tracking-System-main copy" && git pull && pm2 restart ets-server'
# client: rerun the GitHub Actions build and redistribute
```

**Database**

```bash
ssh -t etsadmin@65.21.212.85
cd ~/ets-backups/db && ls -lt | head          # pick a snapshot

# Take the database name from the app's own config. It is NOT "ets" —
# assuming that creates a new empty database while the real one sits
# untouched under a different name.
cd "$HOME/ETS-v5/Employee-Tracking-System-main copy/server"
set -a && . ./.env && set +a
echo "restoring into: $DB_NAME"

pm2 stop ets-server
sudo -u postgres psql -c "ALTER DATABASE $DB_NAME RENAME TO ${DB_NAME}_broken_$(date +%s)"
sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
gzip -dc ~/ets-backups/db/ets-<STAMP>.sql.gz | sudo -u postgres psql -d "$DB_NAME"
pm2 start ets-server
```

Renaming rather than dropping keeps the broken database available for
investigation.

**Screenshots** — restore the relevant day from `~/ets-backups/uploads/<YYYYMMDD>/`.

**Nginx / TLS** — `sudo rm /etc/nginx/sites-enabled/ets && sudo systemctl reload nginx`,
unset `TRUST_PROXY`, restart. Clients on the HTTP build keep working.

---

## Backup verification

Both scripts were run end to end against a real PostgreSQL instance, not
just syntax-checked.

| Check | Result |
|---|---|
| `pg_dump` + gzip -9 | Passed — 6 tables |
| gzip integrity | Passed |
| pg_dump completion marker | Passed |
| Truncated dump detected | **Passed** — a half-truncated dump still passes `gzip -t`; the marker check is what catches it |
| `uploads/` rsync mirror | Passed — 3/3 files |
| 14-day retention pruning | Passed |
| Restore into throwaway DB | **Passed** |
| All tables present after restore | Passed |
| `employees` non-empty after restore | Passed |
| `super_admin` present after restore | Passed — a restore nobody can log into is not a recovery |

Hardlink incremental mirroring means 14 daily snapshots of a 50 GB
`uploads/` cost roughly 50 GB plus daily deltas, not 700 GB.

### Verified on production, 2026-08-04

| Check | Result |
|---|---|
| DB dump | 244 KB, 9 tables |
| Uploads mirror | 55 files, 136 MB |
| Snapshot survives retention | **Passed** after the mtime fix (was 0) |
| Restore into throwaway DB | **Passed** |
| Row counts after restore | 4 employees, 3 configs, 186 attendance, 20007 logs, 97 screenshots |
| super_admin present | **Passed** |

**Limitation:** backups live on the same disk as the data. That covers
accidental deletion, a bad migration, or a botched purge. It does **not**
cover disk failure or losing the VPS. Copy `~/ets-backups` off-site.

**Re-run the restore test** after any schema change, and roughly monthly
otherwise. It is deliberately not on cron: it needs sudo, and a
verification nobody looks at is not a verification.

---

## Monitoring status

**Installed and running on the VPS as of 2026-08-04.**

| Job | Schedule | Output |
|---|---|---|
| Database + uploads backup | 02:00 daily | `~/ets-backup.log` |
| Health check | every 5 min | `~/ets-health.log` |
| Retention purge (rows) | 03:00 Sunday | `~/ets-purge.log` |
| Screenshot file purge | 03:30 Sunday | `~/ets-purge-files.log` |

The health check covers API response, PM2 process state and restart
count, PostgreSQL availability, disk usage (alerts at 80%), and
`uploads/` size. It self-truncates its own log.

**Gap:** alerts are written to a log file, not pushed anywhere. Nobody is
notified unless they look. Wiring the ALERT line to email or a webhook is
listed under future improvements.

---

## Known limitations

1. **Plain HTTP** until Phase 3. Passwords and tokens are readable in transit.
2. **Unsigned binaries** on both platforms. Defender quarantines on
   Windows; Gatekeeper warns on macOS first launch.
3. **Single VPS.** No redundancy. An outage is a full outage.
4. **Backups are on-box.** Protects against mistakes, not hardware loss.
5. **~300 ms baseline latency** from India to the Helsinki server. Fine
   for this traffic pattern, but 40% packet loss was observed on one day
   and produced aborted uploads.
6. **Autostart is user-removable.** The Windows Run key lives in `HKCU`;
   an employee can delete it or disable it from Task Manager. A Windows
   Service would be needed for tamper resistance.
7. **Rate-limit counters are in-process.** `ecosystem.config.js` pins
   `instances: 1`, so this is correct today. Cluster mode would need
   Redis, otherwise each worker keeps its own count.
8. **Screenshot retention is 180 days, logs 90 days**, hardcoded in
   `retention_purge.sql`. Adjust to your compliance policy before the
   first purge runs.
9. **No audit trail for admin reads.** Who viewed whose screenshots is
   not recorded — only writes are.
10. **Comments are mixed Hinglish/English.** No runtime effect;
    translation was explicitly stopped in favour of production work.

---

## Recommended future improvements

Non-blocking, roughly in order of value:

1. **Push alerts** from the health check to email or a webhook. Today a
   failure is only visible to someone reading a log file.
2. **Off-site backup copy** — `rclone` or `scp` `~/ets-backups` to
   another provider nightly.
3. **Code signing** for both platforms. Removes the Defender problem and
   the macOS Gatekeeper warning in one step.
4. **Move the server to an India region.** Cuts ~300 ms to ~20 ms and
   removes the transit-loss exposure that causes aborted uploads. Worth
   doing only if the health log shows the loss recurring.
5. **Admin read auditing** — record who viewed which employee's
   screenshots. Likely to matter for a monitoring product.
6. **Windows Service** for tamper-resistant autostart, if that is a
   requirement rather than a convenience.
7. **Per-employee upload backoff** so a large office reconnecting after
   an outage does not arrive as a single burst.

---

## Declaration

**The project is NOT yet production ready** — but the remaining gap is no
longer inside the repository or the VPS.

Every code-level defect found across this audit has been fixed and
verified. Backups and monitoring are installed on production and proven
by a real restore. Two blockers remain, both external:

- **HTTPS** needs a domain name. Certbot will not issue for a bare IP.
  Passwords and JWTs are in clear text until this is done.
- **Windows code signing** needs a certificate or a policy decision.
  Blocked on one unanswered question: are all employee machines
  company-managed, or will some be personal laptops? Managed-only means a
  free GPO exclusion is enough; any personal machine means a certificate.

**Current honest position:** safe to run a **staged rollout on managed
machines** — start with one real Windows machine and one Mac, then 5–10
employees for a week.

**Do not do a full rollout** before HTTPS is live. Asking staff to click
through a malware warning to install monitoring software that then sends
their password in clear text is a bad position on both counts.
