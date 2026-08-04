# Backup and Restore

Everything here has been run against production, not just written.

## What is backed up

| What | Where from | Frequency | Retention |
|---|---|---|---|
| PostgreSQL database | `pg_dump` | daily 02:00 | 14 days local, 14 days off-site |
| Screenshot files | `server/uploads/` | daily 02:00 | 14 days local, 14 days off-site |
| Server config | `.env`, `ecosystem.config.js`, nginx site, crontab | daily 03:00 | 14 days off-site |

Two layers, because they protect against different things:

- **Local** (`~/ets-backups`) — covers accidental deletion, a bad
  migration, a botched purge. Fast to restore from.
- **Off-site** (rclone remote) — covers disk failure, a compromised VPS,
  or the provider losing the machine. The local copy is worthless in
  those cases because it is on the same disk.

## Schedule

```
02:00 daily    backup.sh            database + uploads, local
03:00 daily    offsite_backup.sh    push local backups + config to rclone remote
03:00 Sunday   retention_purge.sql  delete old rows
03:30 Sunday   purge_screenshots.sh delete orphaned .enc files
every 5 min    healthcheck.sh       API, PM2, PostgreSQL, disk
```

Installed with `bash server/scripts/install_cron.sh` (idempotent).

## One-time off-site setup

```bash
sudo apt install rclone age
rclone config          # create a remote, e.g. "ets-offsite"
```

Then add to `server/.env`:

```
RCLONE_REMOTE=ets-offsite:ets-backups
CONFIG_PASSPHRASE=<long random string — store in a password manager>
```

Then add the job:

```bash
(crontab -l; echo '0 3 * * * bash "$HOME/ETS-v5/Employee-Tracking-System-main copy/server/scripts/offsite_backup.sh" >> "$HOME/ets-offsite.log" 2>&1') | crontab -
```

Run it once by hand first and check the output ends with `verified:`.

### Why the config bundle is encrypted

`.env` holds `DB_PASSWORD`, `JWT_SECRET` and `SCREENSHOT_ENCRYPTION_KEY`.
Uploading it in clear would mean that whoever controls the remote — or
anyone who obtains those credentials — has every secret the system has,
including the key that decrypts all screenshots.

The script encrypts the bundle with `age` before upload and **refuses to
run** if `CONFIG_PASSPHRASE` is unset or `age` is missing, rather than
silently uploading secrets in clear.

Store `CONFIG_PASSPHRASE` in a password manager. Losing it means the
config backup is unrecoverable.

### Why `rclone copy`, not `rclone sync`

`sync` mirrors deletions. If the VPS were wiped or ransomwared, the next
sync would propagate that and destroy the off-site copy too — the exact
scenario this backup exists for. `copy` only adds; retention is applied
separately with `rclone delete --min-age`.

---

## Restore

### Verify a backup is usable (run monthly, and after any schema change)

```bash
bash server/scripts/restore_test.sh
```

Restores the newest dump into a throwaway database, checks every table,
and fails if `employees` is empty or has no `super_admin` — a restore
nobody can log into is not a recovery. Never touches the live database.

Expected:

```
RESTORE TEST PASSED — 4 employees, 1 super admin(s)
```

This is deliberately **not** on cron: it needs sudo, and a verification
nobody looks at is not a verification.

### Restore the database for real

```bash
cd ~/ets-backups/db && ls -lt | head        # pick a snapshot

# Read the real database name from the app's own config. Do NOT assume it
# is "ets" — it is not, and a wrong name here creates a new empty database
# while the real one sits untouched under another name.
cd "$HOME/ETS-v5/Employee-Tracking-System-main copy/server"
set -a && . ./.env && set +a
echo "restoring into: $DB_NAME"

pm2 stop ets-server

# Rename rather than drop, so the broken database stays available
sudo -u postgres psql -c "ALTER DATABASE $DB_NAME RENAME TO ${DB_NAME}_broken_$(date +%s)"
sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
gzip -dc ~/ets-backups/db/ets-<STAMP>.sql.gz | sudo -u postgres psql -d "$DB_NAME"

pm2 start ets-server
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/health   # expect 200
```

### Restore screenshots

```bash
rsync -a ~/ets-backups/uploads/<YYYYMMDD>/ "$HOME/ETS-v5/Employee-Tracking-System-main copy/server/uploads/"
```

Restore the **same day** as the database snapshot. A database newer than
the files leaves rows pointing at screenshots that are not there, and
`purge_screenshots.sh` will not remove them because the rows still exist.

### Restore from off-site

```bash
rclone copy ets-offsite:ets-backups ~/ets-backups-restored
cd ~/ets-backups-restored/db && ls -lt | head
# then follow the database restore steps above against this directory
```

### Restore config

```bash
age -d -o config.tar.gz ~/ets-backups-restored/config-<YYYYMMDD>.tar.gz.age
tar xzf config.tar.gz
# review before copying anything into place — .env contains live secrets
```

### Full VPS loss

1. Provision a new server, install Node, PostgreSQL, pm2, nginx
2. `git clone` the repository
3. `rclone copy` the backups down
4. Restore config, then the database, then uploads
5. Run migrations (idempotent, safe to re-run)
6. `pm2 start ecosystem.config.js && pm2 save && pm2 startup`
7. Point DNS at the new IP, reissue the certificate
8. Run `restore_test.sh` to confirm the new box is backing up correctly

---

## Verified

Run against production on 2026-08-04:

| Check | Result |
|---|---|
| Database dump | 244 KB, 9 tables |
| gzip integrity | passed |
| pg_dump completion marker | passed |
| Truncated dump rejected | passed — a half-truncated dump still passes `gzip -t`; the marker check catches it |
| Uploads mirror | 55 files, 136 MB |
| Snapshot survives retention | passed |
| Restore into throwaway DB | passed |
| Rows after restore | 4 employees, 3 configs, 186 attendance, 20007 logs, 97 screenshots |
| `super_admin` present | passed |
| Restore repeatable | passed — run twice |

## Known limitations

- Off-site retention is time-based, not generational. There is no
  "keep one monthly forever" tier. If corruption goes unnoticed for more
  than 14 days, every copy will have it.
- The health check writes alerts to a log file; nobody is notified
  automatically. Someone has to read `~/ets-health.log`.
- Hardlink mirroring means 14 snapshots of a 50 GB `uploads/` cost about
  50 GB plus daily deltas, not 700 GB — but the off-site copy has no
  hardlinks, so budget remote storage for the full size.
