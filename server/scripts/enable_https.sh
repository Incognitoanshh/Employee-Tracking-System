#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Put the server behind HTTPS.
#
#  This is the single largest gap in the system as delivered: without it,
#  every password and every session token crosses the network in clear
#  text, readable by anyone on the same wifi.
#
#  Everything needed is already written — nginx config, timeouts, trust
#  proxy. The only missing piece is a DOMAIN NAME, because a certificate
#  authority will not issue for a bare IP address. Once one exists and
#  points at this server, this script does the rest.
#
#  BEFORE RUNNING
#    1. Buy or pick a domain, e.g. ets.yourcompany.com
#    2. Add a DNS A record pointing it at this server's public IP
#    3. Wait for it to resolve — this script checks and refuses otherwise
#
#  Usage:
#      bash server/scripts/enable_https.sh ets.yourcompany.com you@company.com
#
#  AFTERWARDS the clients must be rebuilt. They are compiled with the API
#  address baked in, so until that happens they keep talking plain HTTP to
#  port 8000 and the certificate protects nothing. The script prints the
#  exact steps at the end rather than leaving that to be remembered.
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    echo "Usage: bash server/scripts/enable_https.sh <domain> <email>"
    echo "   eg: bash server/scripts/enable_https.sh ets.yourcompany.com you@company.com"
    exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PUBLIC_IP="$(curl -fsS --max-time 10 https://api.ipify.org || echo "")"

echo "═══ 1/6  CHECKING DNS ═══"
RESOLVED="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || echo "")"
if [ -z "$RESOLVED" ]; then
    echo "  ❌ $DOMAIN does not resolve yet."
    echo "     Add a DNS A record for it pointing at ${PUBLIC_IP:-this server}, then"
    echo "     wait a few minutes and run this again."
    exit 1
fi
echo "  $DOMAIN -> $RESOLVED"
if [ -n "$PUBLIC_IP" ] && [ "$RESOLVED" != "$PUBLIC_IP" ]; then
    # Not fatal: the domain may sit behind a proxy. But certbot's HTTP
    # challenge has to reach THIS machine, so a mismatch is usually the
    # reason it fails, and finding out here beats finding out halfway.
    echo "  ⚠️  This server's public IP is $PUBLIC_IP — the record points elsewhere."
    echo "     Certbot's challenge must reach this machine. Continue only if you"
    echo "     know why they differ."
    read -r -p '     Type "yes" to continue: ' GO
    [ "$GO" = "yes" ] || exit 1
fi

echo
echo "═══ 2/6  INSTALLING NGINX AND CERTBOT ═══"
sudo apt-get update -qq
sudo apt-get install -y -qq nginx certbot python3-certbot-nginx
echo "  installed"

echo
echo "═══ 3/6  INSTALLING THE SITE ═══"
sudo cp "$APP_DIR/deploy/nginx-ets.conf" /etc/nginx/sites-available/ets
sudo sed -i "s/ets\.yourdomain\.com/$DOMAIN/g" /etc/nginx/sites-available/ets
sudo ln -sf /etc/nginx/sites-available/ets /etc/nginx/sites-enabled/ets
# The default site answers on port 80 for any hostname and would shadow this.
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
echo "  nginx serving $DOMAIN on port 80"

echo
echo "═══ 4/6  ISSUING THE CERTIFICATE ═══"
sudo certbot --nginx -d "$DOMAIN" --agree-tos -m "$EMAIL" --redirect --non-interactive
echo "  certificate issued and HTTP redirected to HTTPS"

echo
echo "═══ 5/6  TRUSTING THE PROXY ═══"
# Express sees every request as coming from 127.0.0.1 once nginx is in
# front. The login rate limiter keys on IP for its flood ceiling, so
# without this the whole company shares one bucket and the office locks
# itself out. TRUST_PROXY is read by server.js.
ENV_FILE="$APP_DIR/server/.env"
if grep -q "^TRUST_PROXY=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s/^TRUST_PROXY=.*/TRUST_PROXY=1/" "$ENV_FILE"
else
    echo "TRUST_PROXY=1" >> "$ENV_FILE"
fi
pm2 restart ets-server --update-env >/dev/null
echo "  TRUST_PROXY=1, server restarted"

echo
echo "═══ 6/6  CLOSING THE PLAIN PORT ═══"
# 8000 stays reachable on localhost for nginx; it just stops being
# reachable from outside, so nobody can bypass TLS by using the old
# address. Do this LAST — an existing client talking to :8000 breaks here,
# which is exactly why the rebuild below is not optional.
sudo ufw allow 80,443/tcp >/dev/null
sudo ufw deny 8000/tcp >/dev/null
sudo ufw --force enable >/dev/null
sudo ufw status | head -8

echo
echo "═══ VERIFY ═══"
sleep 2
curl -fsS "https://$DOMAIN/api/health" && echo
echo
echo "═══ NOT DONE YET — REBUILD THE CLIENTS ═══"
echo
echo "  Every installed client has the old address compiled into it and is"
echo "  now talking to a port that is closed. Until they are rebuilt and"
echo "  reinstalled, nobody can sign in."
echo
echo "  1. Edit .github/workflows/build.yml, both jobs:"
echo "       API_BASE_URL=https://$DOMAIN/api"
echo "  2. Commit and push to main"
echo "  3. Download the new build and reinstall on every machine"
echo
echo "  Certificate renewal is automatic. Confirm with:"
echo "       sudo certbot renew --dry-run"
