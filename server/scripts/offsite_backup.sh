#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Off-site backup via rclone.
#
#  WHY
#  backup.sh writes to ~/ets-backups, which is on the SAME disk as the
#  data it protects. That covers accidental deletion, a bad migration or
#  a botched purge. It does not cover disk failure, a compromised VPS, or
#  the provider losing the machine. This pushes the same backups to a
#  remote that is not the VPS.
#
#  WHAT IS COPIED
#    ~/ets-backups/db/        PostgreSQL dumps (already gzipped)
#    ~/ets-backups/uploads/   screenshot snapshots (already AES-encrypted)
#    server config            .env, ecosystem.config.js, nginx site
#
#  The .env contains the database password, the JWT secret and the
#  screenshot encryption key, so the config archive is encrypted with
#  age/gpg before it leaves the machine — see CONFIG_PASSPHRASE below.
#  Without that, losing the remote's credentials would hand over every
#  secret the system has.
#
#  SETUP (once, interactive)
#      sudo apt install rclone age
#      rclone config          # create a remote, e.g. "ets-offsite"
#      # then in server/.env:
#      #   RCLONE_REMOTE=ets-offsite:ets-backups
#      #   CONFIG_PASSPHRASE=<a long random string, stored in a password manager>
#
#  USAGE
#      bash server/scripts/offsite_backup.sh
#
#  CRON — runs at 03:00, an hour after the local backup at 02:00
#      0 3 * * * bash /path/to/server/scripts/offsite_backup.sh >> "$HOME/ets-offsite.log" 2>&1
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${ETS_BACKUP_DIR:-$HOME/ets-backups}"
RETENTION_DAYS=14

[ -f "$SERVER_DIR/.env" ] || { echo "ERROR: $SERVER_DIR/.env not found"; exit 1; }
set -a; . "$SERVER_DIR/.env"; set +a

if [ -z "${RCLONE_REMOTE:-}" ]; then
    echo "ERROR: RCLONE_REMOTE is not set in server/.env"
    echo "  Run 'rclone config' first, then add e.g."
    echo "    RCLONE_REMOTE=ets-offsite:ets-backups"
    exit 1
fi

command -v rclone >/dev/null || { echo "ERROR: rclone is not installed"; exit 1; }

echo "=== Off-site backup  $(date "+%Y-%m-%dT%H:%M:%S%z") ==="
echo "remote: $RCLONE_REMOTE"

# ── 1. Server configuration ────────────────────────────────────────────
# Bundled fresh each run so a restore has the settings, not just the data.
CONF_TMP="$(mktemp -d)"
trap 'rm -rf "$CONF_TMP"' EXIT

mkdir -p "$CONF_TMP/config"
for f in "$SERVER_DIR/.env" "$SERVER_DIR/ecosystem.config.js" \
         "$SERVER_DIR/../deploy/nginx-ets.conf" /etc/nginx/sites-available/ets; do
    [ -f "$f" ] && cp "$f" "$CONF_TMP/config/$(basename "$f")" 2>/dev/null || true
done
crontab -l > "$CONF_TMP/config/crontab.txt" 2>/dev/null || true

CONF_ARCHIVE="$BACKUP_DIR/config-$(date +%Y%m%d).tar.gz"
tar czf "$CONF_ARCHIVE" -C "$CONF_TMP" config

# The config archive holds DB_PASSWORD, JWT_SECRET and the screenshot
# encryption key. It must never sit unencrypted on someone else's storage.
if [ -n "${CONFIG_PASSPHRASE:-}" ] && command -v age >/dev/null; then
    printf '%s' "$CONFIG_PASSPHRASE" > "$CONF_TMP/pass"
    age -p -o "$CONF_ARCHIVE.age" < "$CONF_ARCHIVE" 2>/dev/null <<< "$CONFIG_PASSPHRASE" \
        || AGE_FAILED=1
    if [ -f "$CONF_ARCHIVE.age" ] && [ -s "$CONF_ARCHIVE.age" ]; then
        rm -f "$CONF_ARCHIVE"
        echo "[1/3] config bundled and encrypted"
    else
        rm -f "$CONF_ARCHIVE" "$CONF_ARCHIVE.age"
        echo "ERROR: config encryption failed — refusing to upload secrets in clear"
        exit 1
    fi
else
    rm -f "$CONF_ARCHIVE"
    echo "ERROR: CONFIG_PASSPHRASE unset or 'age' not installed."
    echo "  The config bundle contains DB_PASSWORD, JWT_SECRET and the"
    echo "  screenshot encryption key. Refusing to upload it unencrypted."
    echo "  Install age and set CONFIG_PASSPHRASE in server/.env, or remove"
    echo "  this step if you accept keeping config out of the off-site copy."
    exit 1
fi

# ── 2. Sync ────────────────────────────────────────────────────────────
# `copy`, not `sync`: sync would mirror deletions, so a local wipe would
# propagate and destroy the off-site copy too — exactly the scenario this
# backup exists for. Retention is applied separately below.
echo "[2/3] uploading"
rclone copy "$BACKUP_DIR" "$RCLONE_REMOTE" \
    --transfers 4 --checkers 8 \
    --stats-one-line --stats 30s \
    --exclude "*.tmp"

# ── 3. Remote retention ────────────────────────────────────────────────
echo "[3/3] pruning remote older than $RETENTION_DAYS days"
rclone delete "$RCLONE_REMOTE" --min-age "${RETENTION_DAYS}d" --rmdirs || true

# ── Verify ─────────────────────────────────────────────────────────────
# Confirm the newest local dump actually landed and matches in size. An
# upload that silently uploaded nothing is worse than no backup, because
# it looks fine in the log.
NEWEST="$(find "$BACKUP_DIR/db" -name 'ets-*.sql.gz' | sort | tail -1)"
if [ -n "$NEWEST" ]; then
    NAME="$(basename "$NEWEST")"
    LOCAL_SIZE="$(stat -c %s "$NEWEST" 2>/dev/null || stat -f %z "$NEWEST")"
    REMOTE_SIZE="$(rclone size "$RCLONE_REMOTE/db/$NAME" --json 2>/dev/null \
                   | python3 -c 'import sys,json; print(json.load(sys.stdin).get("bytes",0))' 2>/dev/null || echo 0)"
    if [ "$LOCAL_SIZE" = "$REMOTE_SIZE" ]; then
        echo "  verified: $NAME ($LOCAL_SIZE bytes) present on remote"
    else
        echo "  FAILED: $NAME is $LOCAL_SIZE bytes locally but $REMOTE_SIZE on remote"
        exit 1
    fi
fi

echo
rclone about "$(echo "$RCLONE_REMOTE" | cut -d: -f1):" 2>/dev/null || true
echo "remote total: $(rclone size "$RCLONE_REMOTE" 2>/dev/null | tail -1)"
echo "=== done ==="
