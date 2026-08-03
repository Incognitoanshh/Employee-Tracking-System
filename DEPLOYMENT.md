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
will not issue a certificate for a bare IP. Substitute your domain for
`ets.example.com` throughout.

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

One manual dump exists. There is no schedule and no restore test.

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
