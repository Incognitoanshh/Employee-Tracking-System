#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Pending migrations ko database pe apply karta hai — as postgres.
#
#  YE KYUN CHAHIYE. App ka database user tables ka MAALIK nahi hai. Wo rows
#  padh-likh sakta hai, par schema nahi badal sakta — aur ye SAHI hai: jo
#  service internet ke saamne khuli hai use ALTER TABLE ka haq nahi hona
#  chahiye. Isliye server boot pe migration lagane ki koshish karta hai,
#  "must be owner of table …" sunta hai, aur ek line likh kar is script ka
#  naam le leta hai.
#
#  Deploy `git pull && pm2 restart` hai — wo migration NAHI chalata. Jis din
#  release me koi migration ho, us din ye script chalani hai; warna naya code
#  purane schema pe chalega aur page pe dash aayenge.
#
#  Chalane ka tarika (VPS pe):
#      bash server/scripts/migrate.sh
#
#  Sirf dekhna ho ki kya baaki hai, lagana na ho:
#      bash server/scripts/migrate.sh --dry-run
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATIONS_DIR="$APP_DIR/migrations"

# Database ka naam app ki apni .env se — wahi jagah jahan se backup.sh,
# restore_test.sh aur reset_test_data.sh bhi padhte hain. Yahan hardcode
# karne se wahi bug hota hai jo reset_test_data.sh me tha: script chalti rahi
# aur har psql call "database does not exist" pe fail hoti rahi.
if [ ! -f "$APP_DIR/.env" ]; then
    echo "ERROR: $APP_DIR/.env nahi mili — kya ye sahi jagah se chal raha hai?" >&2
    exit 1
fi
set -a
# shellcheck disable=SC1091
. "$APP_DIR/.env"
set +a

DB="${DB_NAME:-}"
if [ -z "$DB" ]; then
    echo "ERROR: .env me DB_NAME nahi hai." >&2
    exit 1
fi

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

# postgres ke roop me — yahi is script ka poora maqsad hai.
psql_super() { sudo -u postgres psql -d "$DB" -v ON_ERROR_STOP=1 "$@"; }

# Wahi record jo server padhta hai, aur wahi shakl. Agar ye table nahi hai to
# server ne abhi tak boot pe koshish nahi ki — bana dena surakshit hai.
psql_super -q -c "
    CREATE TABLE IF NOT EXISTS schema_migrations (
        name        TEXT PRIMARY KEY,
        applied_at  TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
    )" > /dev/null

applied=0
skipped=0
failed=0

# Naam se sorted — wahi kram jo server/utils/migrate.js aur tests/_migrate.js
# use karte hain, aur wahi wajah jisse migrations pe date aur sequence number
# hota hai.
#
# `._` se shuru hone wali files CHHODI jaati hain. macOS har us file ke saath
# ek AppleDouble chhod deta hai jo Mac se copy hui ho; wo binary hoti hain aur
# psql unpe "invalid message format" deta hai.
for path in $(find "$MIGRATIONS_DIR" -maxdepth 1 -name '*.sql' ! -name '._*' | sort); do
    name="$(basename "$path")"

    # retention_purge.sql ek scheduled job hai, schema change nahi — wahi
    # exclusion jo doosri dono jagah hai.
    [ "$name" = "retention_purge.sql" ] && continue

    already="$(psql_super -tAc \
        "SELECT 1 FROM schema_migrations WHERE name = '$name'" || true)"
    if [ "$already" = "1" ]; then
        skipped=$((skipped + 1))
        continue
    fi

    if [ "$DRY_RUN" = "1" ]; then
        echo "PENDING  $name"
        applied=$((applied + 1))
        continue
    fi

    echo "── $name"
    # Ek transaction me: aadhi lagi migration wo schema chhod jaati hai jiske
    # liye koi code likha hi nahi gaya.
    if psql_super -1 -q -f "$path" \
        && psql_super -q -c "INSERT INTO schema_migrations (name) VALUES ('$name')
                             ON CONFLICT (name) DO NOTHING" > /dev/null; then
        echo "   applied"
        applied=$((applied + 1))
    else
        echo "   FAILED — ye migration lagi NAHI hai, aur record me bhi nahi gayi." >&2
        failed=$((failed + 1))
    fi
done

echo
if [ "$DRY_RUN" = "1" ]; then
    echo "$applied pending, $skipped pehle se lagi hui.  (--dry-run: kuch badla nahi)"
    exit 0
fi

echo "$applied lagayi, $skipped pehle se thi, $failed fail."
if [ "$failed" -gt 0 ]; then
    echo
    echo "Server ko restart karne ka koi fayda nahi jab tak ye theek na ho —" >&2
    echo "naya code purane schema pe chalega." >&2
    exit 1
fi

echo
echo "Ab server restart karein:   pm2 restart ets-server"
echo "Aur jaanch lein:            curl -s localhost:8000/api/health"
