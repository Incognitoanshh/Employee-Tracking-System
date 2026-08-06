# ETS — Production Deployment Checklist

**Release:** `90ed7e6` · **Build:** run `30805656701` · **Server:** `65.21.212.85`

Work top to bottom. Every step has a verification command — do not move on
until it passes. Anything marked **BLOCKER** must be done before employees
get the app.

---

## 0. Current state

| Component | Status |
|---|---|
| GitHub `main` | `90ed7e6` |
| CI build (Windows + macOS) | ✅ success, encryption key injected |
| VPS server code | ⚠️ **behind** — audit fixes not deployed |
| HTTPS / TLS | ❌ **not configured — BLOCKER** |
| Backups | ⚠️ one manual dump only (`~/ets_backup_2026-08-02_1631.sql`) |
| Monitoring | ❌ none |

The CI build only packages the **client**. `server/config/db.js` (connection
pool sizing) and `retention_purge.sql` reach production only via step 2.

---

## 1. HTTPS — Nginx + Let's Encrypt  🔴 BLOCKER

Right now the API is `http://65.21.212.85:8000`. Every employee login sends
the **password and JWT in cleartext** over the internet, and the token stays
replayable for 24 hours. Screenshot *contents* are safe (AES-256-GCM applied
on the device); credentials are not.

**Prerequisite:** a domain A-record pointing at `65.21.212.85`. Let's Encrypt
will not issue a certificate for a bare IP.

**This is now one command.** Get the domain, point it here, then:

```bash
bash server/scripts/enable_https.sh ets.yourcompany.com you@company.com
```

It checks DNS first and refuses if the record is missing, installs nginx and
certbot, issues the certificate, sets `TRUST_PROXY=1` (without which nginx
makes every request look like `127.0.0.1` and one rate-limit bucket locks out
the whole office), closes port 8000 to the outside, and verifies over HTTPS.

It finishes by telling you the part that is easy to forget: **the clients must
be rebuilt.** Each one has the API address compiled in, so until
`API_BASE_URL` in `.github/workflows/build.yml` is changed to
`https://ets.yourcompany.com/api` and the new build installed everywhere,
nobody can sign in — the port they know is now closed.

The manual steps below are kept as reference for what the script does.

Substitute your domain for `ets.example.com` throughout.

```bash
# 1.1 — DNS propagated?
dig +short ets.example.com          # must print 65.21.212.85
```

```bash
# 1.2 — install nginx + certbot
ssh etsadmin@65.21.212.85 'sudo apt update && sudo apt install -y nginx certbot python3-certbot-nginx'
```

```bash
# 1.3 — reverse proxy config
ssh etsadmin@65.21.212.85 'sudo tee /etc/nginx/sites-available/ets > /dev/null <<'"'"'NGINX'"'"'
server {
    listen 80;
    server_name ets.example.com;

    # Screenshot uploads are ~200-400 KB; 10 MB matches the multer limit.
    client_max_body_size 10M;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}
NGINX
sudo ln -sf /etc/nginx/sites-available/ets /etc/nginx/sites-enabled/ets
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx'
```

```bash
# 1.4 — issue the certificate (certbot rewrites the vhost for TLS + redirect)
ssh -t etsadmin@65.21.212.85 'sudo certbot --nginx -d ets.example.com --agree-tos -m hello@amazeinternet.com --redirect'
```

```bash
# 1.5 — close port 8000 to the world; nginx reaches it on loopback
ssh etsadmin@65.21.212.85 'sudo ufw allow 80,443/tcp && sudo ufw deny 8000/tcp && sudo ufw --force enable && sudo ufw status'
```

**Verify**

```bash
curl -s https://ets.example.com/api/health                 # healthy JSON
curl -s -o /dev/null -w "%{http_code}\n" http://ets.example.com/api/health   # 301
curl -s --max-time 8 http://65.21.212.85:8000/api/health || echo "port 8000 closed ✅"
ssh etsadmin@65.21.212.85 'sudo certbot renew --dry-run'   # auto-renewal works
```

### After TLS: point the client at HTTPS

The API URL is baked into the build. Until this changes, distributed clients
keep using plain HTTP even though the server supports TLS.

```
.github/workflows/build.yml  — both jobs:
  API_BASE_URL=https://ets.example.com/api
```

Also update `ALLOWED_ORIGIN` in the server `.env` to the HTTPS origin, then
push — CI rebuilds, and **that** build is the one to distribute.

---

## 2. Deploy server audit fixes

```bash
cd "/Users/ansh/Downloads/Employee-Tracking-System-main copy" && tar czf - -C server config/db.js migrations/retention_purge.sql | ssh etsadmin@65.21.212.85 'cd "/home/etsadmin/ETS-v5/Employee-Tracking-System-main copy/server" && tar xzf - && pm2 restart ets-server && sleep 3 && curl -s localhost:8000/api/health'
```

**Verify**

```bash
ssh etsadmin@65.21.212.85 'pm2 logs ets-server --lines 20 --nostream | grep -E "DB CONNECTED|ERROR"'
```

Expect `✅ DB CONNECTED`, no `[DB POOL]` errors.

---

## 3. Backups  🔴 BLOCKER

Nightly dumps, retention and a restore test are all in place and verified.
What is **not** in place is anywhere off this machine to put them:
`~/ets-backups` sits on the same disk as the data it protects. That covers a
bad migration or an accidental delete; it does not cover the disk failing or
the provider losing the VPS.

**This is now one command.** It needs somewhere to put the backups, and that
does not have to cost anything: a Google account already provides 15 GB free,
which is far more than these backups need — the database dump is measured in
kilobytes and the screenshots are already compressed. rclone speaks Google
Drive natively.

```bash
bash server/scripts/setup_offsite.sh
```

rclone asks for the credentials in its own prompts and keeps them in its own
config; nothing else reads or stores them. The script then generates a
passphrase for the encrypted config archive — **write that down somewhere
that is not this server**, because the point of it is that you still have it
when this server is gone — and finally runs a real backup and lists what
landed on the remote. A backup job that has never run is not a backup.

The historical steps below are kept as reference.

```bash
# 3.1 — nightly dump, 14-day retention
ssh etsadmin@65.21.212.85 'mkdir -p ~/backups && tee ~/backup-ets.sh > /dev/null <<'"'"'SH'"'"'
#!/bin/bash
set -euo pipefail
cd "/home/etsadmin/ETS-v5/Employee-Tracking-System-main copy/server"
set -a; . ./.env; set +a
STAMP=$(date +%F_%H%M)
PGPASSWORD="$DB_PASSWORD" pg_dump -h localhost -U "$DB_USER" "$DB_NAME" | gzip > ~/backups/ets_$STAMP.sql.gz
find ~/backups -name "ets_*.sql.gz" -mtime +14 -delete
echo "$(date -Is) backup ok: ets_$STAMP.sql.gz"
SH
chmod +x ~/backup-ets.sh && ~/backup-ets.sh'
```

```bash
# 3.2 — schedule 02:00 daily
ssh etsadmin@65.21.212.85 '(crontab -l 2>/dev/null | grep -v backup-ets; echo "0 2 * * * /home/etsadmin/backup-ets.sh >> /home/etsadmin/backups/backup.log 2>&1") | crontab - && crontab -l'
```

```bash
# 3.3 — RESTORE TEST. An untested backup is not a backup.
ssh -t etsadmin@65.21.212.85 'cd "/home/etsadmin/ETS-v5/Employee-Tracking-System-main copy/server" && set -a && . ./.env && set +a && sudo -u postgres createdb ets_restore_test && gunzip -c ~/backups/$(ls -t ~/backups | head -1) | sudo -u postgres psql -d ets_restore_test > /dev/null && sudo -u postgres psql -d ets_restore_test -c "SELECT (SELECT COUNT(*) FROM employees) AS employees, (SELECT COUNT(*) FROM attendance) AS attendance;" && sudo -u postgres dropdb ets_restore_test'
```

Row counts must match production. **Screenshot files are not in the dump** —
back up `server/uploads/` separately (rsync/object storage) or accept that a
DB-only restore leaves screenshot rows pointing at missing files.

---

## 4. Monitoring  🟠

```bash
# 4.1 — pm2 restarts on reboot
ssh -t etsadmin@65.21.212.85 'pm2 save && pm2 startup systemd -u etsadmin --hp /home/etsadmin'
```

```bash
# 4.2a — push the alerts somewhere a person will see them (FREE, no account)
#
#   Alerts used to reach only ~/ets-health.log. A monitor that tells you
#   after you have already noticed is not a monitor.
#
#   1. Install the ntfy app (Android/iOS/web)
#   2. Subscribe to a long random topic, e.g.  ets-alerts-8fj3kd92ms
#   3. Put it in server/.env:  ETS_NTFY_TOPIC=ets-alerts-8fj3kd92ms
#
#   The topic IS the secret — anyone who knows it reads your alerts. The
#   messages carry no credentials or employee data, only which check failed.
#   Repeats are throttled to one an hour per problem, so a server that stays
#   down does not send twelve alerts an hour until somebody mutes it.
#
#   Test it:  ETS_NTFY_TOPIC=<topic> bash server/scripts/healthcheck.sh

# 4.2 — health check every 5 min; alerts on two consecutive failures
ssh etsadmin@65.21.212.85 'tee ~/health-check.sh > /dev/null <<'"'"'SH'"'"'
#!/bin/bash
STATE=~/.ets_health_fails
FAILS=$(cat $STATE 2>/dev/null || echo 0)
if curl -sf --max-time 10 http://localhost:8000/api/health > /dev/null; then
  [ "$FAILS" -ge 2 ] && echo "$(date -Is) RECOVERED" >> ~/health.log
  echo 0 > $STATE
else
  FAILS=$((FAILS+1)); echo $FAILS > $STATE
  echo "$(date -Is) health check failed ($FAILS)" >> ~/health.log
  if [ "$FAILS" -eq 2 ]; then
    echo "$(date -Is) ALERT: ETS API down, restarting" >> ~/health.log
    pm2 restart ets-server
  fi
fi
SH
chmod +x ~/health-check.sh
(crontab -l 2>/dev/null | grep -v health-check; echo "*/5 * * * * /home/etsadmin/health-check.sh") | crontab -'
```

```bash
# 4.3 — disk alarm. uploads/ grows ~40 GB/day at 1000 employees without retention.
ssh etsadmin@65.21.212.85 'tee ~/disk-check.sh > /dev/null <<'"'"'SH'"'"'
#!/bin/bash
USE=$(df / | awk "NR==2{print \$5}" | tr -d %)
[ "$USE" -gt 80 ] && echo "$(date -Is) DISK ${USE}% — run retention_purge.sql" >> ~/health.log
SH
chmod +x ~/disk-check.sh
(crontab -l 2>/dev/null | grep -v disk-check; echo "0 * * * * /home/etsadmin/disk-check.sh") | crontab -'
```

```bash
# 4.4 — data retention, weekly (Sunday 03:00). Review the periods first.
ssh etsadmin@65.21.212.85 '(crontab -l 2>/dev/null | grep -v retention_purge; echo "0 3 * * 0 cd \"/home/etsadmin/ETS-v5/Employee-Tracking-System-main copy/server\" && set -a && . ./.env && set +a && PGPASSWORD=\$DB_PASSWORD psql -h localhost -U \$DB_USER -d \$DB_NAME -f migrations/retention_purge.sql >> /home/etsadmin/purge.log 2>&1") | crontab - && crontab -l'
```

**Weekly manual check**

```bash
ssh etsadmin@65.21.212.85 'df -h / | tail -1; du -sh ~/ETS-v5/*/server/uploads 2>/dev/null; tail -5 ~/health.log; pm2 list'
```

---

## 5. Rollout

Do **not** push the app to 1000 employees at once.

### What every employee will hit, on every machine

**macOS — Screen Recording.** The first capture fails until it is granted, and
the app cannot ask for it on the employee's behalf. System Settings → Privacy
& Security → **Screen & System Audio Recording** → enable **Amaze ETS**, then
quit from the tray icon (not just closing the window) and reopen. Installing a
newer build asks again — macOS ties the permission to the app bundle, and a
rebuilt bundle is a new one to it.

**Windows — Defender.** The app is unsigned, so SmartScreen blocks it and
Defender may quarantine it outright. These are personal machines, so Group
Policy is not available. Until a certificate is bought, each employee has to:

1. Click **More info → Run anyway** on the SmartScreen prompt
2. If the file disappears, restore it from Windows Security → Protection
   history, and add the install folder to exclusions

That is a poor first impression and it will generate support calls. It is the
strongest argument for buying a certificate before a wide rollout.

### Free first: report the false positive to Microsoft

Before spending anything, submit the build to Microsoft as a false positive.
It costs nothing, takes about ten minutes, and Defender's cloud definitions
usually update within a few days — after which the quarantine stops happening
for everybody, on every machine, without a certificate.

  https://www.microsoft.com/en-us/wdsi/filesubmission

Submit as a **software developer**, attach `Amaze ETS.exe` from the CI
artifact, and say what it is: an internal employee monitoring client, built
with PyInstaller, distributed only inside the company. PyInstaller binaries
are flagged constantly for exactly this reason and the submission process
exists for it.

This has to be redone whenever the executable changes materially, which is
why it relieves the problem rather than solving it. A certificate solves it.

### Code signing — what it costs and what it fixes

An OV code-signing certificate runs roughly $200–400/year (Sectigo, DigiCert,
SSL.com) and takes a few days of business verification. An EV certificate
costs more and clears SmartScreen immediately rather than building reputation
over time.

Once bought, signing goes into `.github/workflows/build.yml` after the
PyInstaller step, with the certificate and password held as repository
secrets — never committed:

```yaml
- name: Sign the Windows executable
  run: |
    echo "${{ secrets.WINDOWS_CERT_BASE64 }}" | base64 -d > cert.pfx
    signtool sign /f cert.pfx /p "${{ secrets.WINDOWS_CERT_PASSWORD }}" \
      /tr http://timestamp.digicert.com /td sha256 /fd sha256 \
      "dist/Amaze ETS.exe"
    rm cert.pfx
```

Timestamping matters: without `/tr`, every signed build stops validating the
day the certificate expires.

macOS has the same problem in a milder form — an unsigned app needs
right-click → Open the first time. An Apple Developer ID is $99/year and
removes that too.

**Stage 1 — one Windows machine.** The Windows launch-crash fix has only been
verified by a local PyInstaller build on macOS; it has never run on real
Windows. This is the single highest-risk untested path.

```bash
gh run download 30805656701 --dir ~/Downloads/ETS-release
```

Install on one Windows PC, sign in as an employee, and confirm: app opens,
screenshots appear in the admin panel within the shift, Recent Activity fills,
logout works.

**Stage 2 — 5 employees, one full shift.** Then check:

```bash
ssh etsadmin@65.21.212.85 'cd "/home/etsadmin/ETS-v5/Employee-Tracking-System-main copy/server" && set -a && . ./.env && set +a && PGPASSWORD=$DB_PASSWORD psql -h localhost -U $DB_USER -d $DB_NAME -c "SELECT employee_id, COUNT(*) shots, MIN(created_at::time) first, MAX(created_at::time) last FROM screenshots WHERE created_at > NOW() - INTERVAL '\''1 day'\'' GROUP BY 1;"'
```

Captures should be **spread across the shift**, not bunched at the start.
Bunching means the old build is still installed.

**Stage 3 — department. Stage 4 — company-wide.**

Before each stage: `df -h`, `pm2 list`, `tail ~/health.log`.

**Admin accounts:** super admin max 3, admin max 20 (enforced server-side).
Only a super admin can create or remove admins.

---

## 6. Rollback

**Client** — reinstall the previous artifact; no server change needed. Old and
new clients speak the same API.

| Build | Commit | Notes |
|---|---|---|
| 30805656701 | `90ed7e6` | current |
| 30802521320 | `eefdb84` | previous good |
| 30800366315 | `56d18c1` | ⚠️ first attempt had an empty encryption key |

**Server**

```bash
ssh -t etsadmin@65.21.212.85 'cd "/home/etsadmin/ETS-v5/Employee-Tracking-System-main copy" && git log --oneline -5 && git checkout <previous-sha> -- server/ && pm2 restart ets-server && sleep 3 && curl -s localhost:8000/api/health'
```

**Database**

```bash
ssh -t etsadmin@65.21.212.85 'pm2 stop ets-server && cd "/home/etsadmin/ETS-v5/Employee-Tracking-System-main copy/server" && set -a && . ./.env && set +a && sudo -u postgres dropdb $DB_NAME && sudo -u postgres createdb -O $DB_USER $DB_NAME && gunzip -c ~/backups/<chosen>.sql.gz | sudo -u postgres psql -d $DB_NAME && pm2 start ets-server'
```

Migrations are additive and idempotent — none drop a column or table, so a
newer client against an older schema degrades (blank name/designation) rather
than erroring.

**Encryption key must never be rotated.** Rotating it makes every existing
screenshot permanently unreadable. It lives in GitHub Actions secrets and in
`client/.env`. The old key is still in git history at commit `fa39080`
(private repo — accepted risk, documented deliberately).

---

## 7. Known limitations

| Item | Impact | Suggested |
|---|---|---|
| `uploads/` has no retention | disk fills | extend `retention_purge.sql` |
| `unhandledRejection` logs only, no exit | possible undefined state | let PM2 restart |
| Tab switch does not reload | up to 30 s stale | Refresh button exists |
| `idle_logs`, `sessions`, `shifts` tables unused | clutter | drop after a backup |
| Screenshot quality fixed at 1920px / q75 | tune per need | `SCREENSHOT_MAX_WIDTH`, `SCREENSHOT_JPEG_QUALITY` |
| Rate limit store is per-process | breaks in pm2 cluster mode | Redis store if scaling out |

---

## Sign-off

- [ ] HTTPS live, port 8000 closed, cert auto-renews
- [ ] `API_BASE_URL` switched to HTTPS, rebuilt, that build distributed
- [ ] Server audit fixes deployed, `DB CONNECTED` clean
- [ ] Nightly backup running **and restore-tested**
- [ ] pm2 startup, health check, disk alarm, retention cron in place
- [ ] Stage 1 passed on real Windows
- [ ] Stage 2 shows screenshots spread across the shift
- [ ] Rollback artifact and DB dump identified and reachable

---

## Windows Defender false positive

Windows Defender quarantines the build ("Threats found", app disappears
from the folder). Confirmed on Windows Server 2022 with the 2026-08-03
build.

This is a false positive, not a compromised binary — but it is a
rollout blocker, because it will happen on every employee machine.

### Why it happens

Nothing in the code is malicious. The build trips heuristics because it
looks exactly like what heuristics are designed to catch:

- unsigned executable from an unknown publisher (no reputation)
- PyInstaller self-extracting stub (`--onefile` unpacks itself to
  `%TEMP%` at launch — the same behaviour malware packers use)
- captures the screen on a timer
- registers itself for autostart in the Run key
- uploads data to a hardcoded IP over plain HTTP

Any one of these is fine. Together they read as spyware to a scanner
that has never seen this binary before.

A quarantine mid-run also produces a confusing secondary symptom: the
app is still running, but its own executable is gone, so the next lazy
import fails with `[Errno 22] Invalid argument: '...\Amaze ETS.exe'`.
That error surfaces at the login screen and looks like a login bug. It
is not — it is the quarantine.

### What has already been done

- `--noupx` in the Windows build. PyInstaller compresses with UPX when
  available, and UPX packing is one of the strongest AV signals.
  Removing it costs a few MB.

This lowers the odds. It does not solve it.

### The actual fix, in order of preference

1. **Code-signing certificate** (recommended). An OV certificate
   removes "unknown publisher". An EV certificate additionally grants
   immediate SmartScreen reputation, which an OV cert has to build up
   over time and downloads. Sign `Amaze ETS.exe` as a build step.
   This is the only option that fixes the problem for machines outside
   your control.

2. **Defender exclusion pushed by policy.** Since these are company
   machines, an exclusion for the install path can be deployed via GPO
   or Intune. Works immediately and costs nothing, but only covers
   managed machines, and a path exclusion is a real reduction in
   endpoint security — scope it to the install directory, never to
   `%TEMP%` or the whole user profile.

3. **Submit the binary to Microsoft as a false positive**
   (https://www.microsoft.com/en-us/wdsi/filesubmission). Free, usually
   resolved in a few days. The catch: the verdict is tied to that exact
   file hash, so every new build needs resubmitting. Practical for
   tagged releases, not for routine builds.

### Personal machines — decided 2026-08-04

The application will be installed on employees' **personal** Windows
machines, not company-managed devices. That removes option 2: there is no
GPO or Intune reach into a personal laptop, and asking someone to add a
Defender exclusion on their own computer for an employer's monitoring
tool is both a hard sell and bad security advice.

**Recommended path until a certificate is purchased:**

1. **Submit each release build to Microsoft as a false positive**
   (https://www.microsoft.com/en-us/wdsi/filesubmission). Free, usually
   cleared in 1-3 days. The verdict is tied to the exact file hash, so
   this only works if releases are infrequent and planned — submit, wait
   for the verdict, then distribute. It does not work for a build-a-day
   cadence.

2. **Ship `--onedir`, not `--onefile`.** A self-extracting stub that
   unpacks 62 MB into `%TEMP%` at every launch is one of the patterns
   heuristics weight most heavily, and it also means Defender rescans the
   extracted DLLs on each start — the likely cause of the multi-second
   startup freeze observed on Windows Server 2022. onedir keeps the DLLs
   in place and avoids both.

3. **Publish a short install guide** with a screenshot of the exact
   SmartScreen dialog and the "More info -> Run anyway" path, plus the
   SHA-256 of the release so a cautious employee can verify what they are
   running. Being upfront about the warning is far better than staff
   discovering it alone and assuming the download is malware.

**Buy the certificate.** For personal-device deployment it is not
optional in practice — it is the only option that works without asking
every employee to override their own antivirus. An **EV** certificate is
worth the extra cost over OV here, because it grants SmartScreen
reputation immediately; an OV certificate has to earn reputation over
time and downloads, which means early employees still see warnings.

### Do not

- Tell employees to disable Defender, or to add a blanket exclusion for
  their whole Downloads folder or user profile. That trades a cosmetic
  problem for a real one — and on a personal machine it is their own
  security you are weakening, not the company's.

### Until this is resolved

Do not roll out to employee machines. Every install will need manual
intervention, and asking staff to click through a malware warning to
install monitoring software is a bad position to be in — both for
security posture and for how the rollout is received.


---

## Screenshot volume policy

The screenshot count is **per IST calendar day**, not per shift. Range 1-20,
default 10.

It used to be per shift, and that was unsafe. Because the count was tied to
the shift, an employee logged in outside their shift fell onto a separate
code path that ignored the count entirely and captured every min..max
minutes. Measured in production: **167 captures in 24h and 334 in 48h**
where the admin had configured 10. At 1000 employees that is ~33 GB/day
instead of the ~1.5 GB/day the disk was sized for — the volume fills in
about 3 days rather than 70.

Two things now guarantee the number:

1. **Scheduling** spreads the day's remaining budget across the remaining
   monitoring window, with random placement inside equal slots so the
   timing stays unpredictable.
2. **A hard cap in `capture_screenshot()`** refuses once the day's budget
   is spent. It counts rows in the local database for the current IST day,
   so it survives an app restart or a logout/login — an in-memory counter
   could be reset by quitting the app.

The cap is the guarantee. Rescheduling (a config change, the midnight
rollover) can move *when* captures happen but can never raise *how many*.

A skipped capture is logged with `log()`, not `log_verbose()`, so an admin
can tell the difference between "budget reached" and "tracking broken".

`screenshot_max_minutes` is now advisory. With a daily budget the spacing
follows from how much of the day is left; `screenshot_min_minutes` still
acts as a floor between consecutive captures.

**Overnight shifts:** the budget is per calendar day, so a 22:00-06:00
worker receives the configured number before midnight and again after. That
is per specification.

Storage at 1000 employees, 10/day, ~200 KB each: **~2 GB/day**.
