#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Set up the off-site backup, once.
#
#  WHY THIS MATTERS
#  backup.sh writes to ~/ets-backups — the same disk as the data it
#  protects. That covers a bad migration or an accidental delete. It does
#  not cover the disk failing, the VPS being compromised, or the provider
#  losing the machine. In any of those, the backups go with the data.
#
#  offsite_backup.sh already handles the copying. What it needs is an
#  rclone remote to copy TO, and a passphrase to encrypt the config
#  archive with. This walks through both and then proves it works.
#
#  YOU WILL BE ASKED FOR CREDENTIALS by rclone itself, in its own prompts.
#  Nothing here reads, stores or echoes them — rclone keeps them in its own
#  config file with permissions only this user can read.
#
#  Usage:
#      bash server/scripts/setup_offsite.sh
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$APP_DIR/server/.env"

echo "═══ 1/5  RCLONE ═══"
if ! command -v rclone >/dev/null; then
    echo "  installing rclone…"
    sudo -v
    curl -fsS https://rclone.org/install.sh | sudo bash
fi
echo "  $(rclone version | head -1)"

echo
echo "═══ 2/5  CHOOSE A REMOTE ═══"
EXISTING="$(rclone listremotes 2>/dev/null || true)"
if [ -n "$EXISTING" ]; then
    echo "  Already configured:"
    echo "$EXISTING" | sed 's/^/    /'
    echo
    read -r -p "  Use one of these? Type its name with the colon, or press Enter to add a new one: " REMOTE
fi

if [ -z "${REMOTE:-}" ]; then
    echo
    echo "  rclone will now ask its own questions. Suggested answers:"
    echo "    name    : ets-offsite"
    echo "    storage : drive (Google Drive)  or  s3  or  b2"
    echo "    the rest: press Enter for defaults"
    echo
    echo "  On a server with no browser, choose 'N' for auto config and follow"
    echo "  the instructions it prints — you authorise it from your own laptop."
    echo
    read -r -p "  Press Enter to start rclone config… " _
    rclone config
    echo
    rclone listremotes | sed 's/^/    /'
    read -r -p "  Which remote did you create? (with the colon, eg ets-offsite:) " REMOTE
fi

REMOTE="${REMOTE%/}"
case "$REMOTE" in
    *:) ;;
    *) REMOTE="${REMOTE}:" ;;
esac

echo
echo "═══ 3/5  TESTING THE REMOTE ═══"
if ! rclone lsd "$REMOTE" >/dev/null 2>&1; then
    echo "  ❌ Cannot reach $REMOTE — check the config and run this again."
    exit 1
fi
echo "  $REMOTE reachable"

echo
echo "═══ 4/5  PASSPHRASE FOR THE CONFIG ARCHIVE ═══"
# The .env holds the database password, the JWT secret and the screenshot
# encryption key. It is encrypted before it leaves the machine so that
# losing the remote's credentials does not hand over everything else too.
#
# Generated here rather than typed: a passphrase someone invents on the
# spot is the weakest link in a chain that is otherwise AES.
if grep -q "^CONFIG_PASSPHRASE=" "$ENV_FILE" 2>/dev/null; then
    echo "  Already set — leaving it alone."
else
    PASSPHRASE="$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 32)"
    printf '\nCONFIG_PASSPHRASE=%s\n' "$PASSPHRASE" >> "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo
    echo "  ┌──────────────────────────────────────────────────────────────┐"
    echo "  │  WRITE THIS DOWN SOMEWHERE THAT IS NOT THIS SERVER.          │"
    echo "  │  Without it the encrypted config backup cannot be opened,    │"
    echo "  │  and the whole point of it is that you still have it when    │"
    echo "  │  this server is gone.                                        │"
    echo "  └──────────────────────────────────────────────────────────────┘"
    echo
    echo "      $PASSPHRASE"
    echo
    read -r -p "  Typed it somewhere safe? Type \"yes\": " SAVED
    [ "$SAVED" = "yes" ] || { echo "  Stopping — run again when you are ready."; exit 1; }
fi

if grep -q "^RCLONE_REMOTE=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^RCLONE_REMOTE=.*|RCLONE_REMOTE=$REMOTE|" "$ENV_FILE"
else
    printf 'RCLONE_REMOTE=%s\n' "$REMOTE" >> "$ENV_FILE"
fi
echo "  RCLONE_REMOTE=$REMOTE"

echo
echo "═══ 5/5  PROVING IT WORKS ═══"
# A backup job that has never run is not a backup. Run it once now, and
# read back what landed — an empty remote here means the schedule below
# would have been quietly doing nothing every night.
bash "$APP_DIR/server/scripts/backup.sh"
echo
bash "$APP_DIR/server/scripts/offsite_backup.sh"
echo
echo "  what is now on the remote:"
rclone ls "$REMOTE" --max-depth 3 2>/dev/null | head -20 | sed 's/^/    /'

echo
echo "═══ DONE ═══"
echo "  Add the nightly job if install_cron.sh has not already:"
echo "      bash server/scripts/install_cron.sh"
echo
echo "  Check it is still running, occasionally:"
echo "      rclone ls $REMOTE --max-depth 2 | tail"
