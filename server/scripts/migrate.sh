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
#
# MIGRATE_NO_SUDO=1 se ye sudo ke bina chalta hai, aur SIRF isliye hai ki ye
# script khud test ki ja sake. Pehli baar ye bina chalaye bheji gayi thi aur
# production pe hi tooti — is baar tests/test_migrate_sh.sh ise asli files pe
# chalata hai, ek aisi directory me jiske naam me space hai.
if [ -n "${MIGRATE_NO_SUDO:-}" ]; then
    psql_super() { psql -d "$DB" -v ON_ERROR_STOP=1 "$@"; }
else
    psql_super() { sudo -u postgres psql -d "$DB" -v ON_ERROR_STOP=1 "$@"; }
fi

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

# GLOB, `find | sort` NAHI — aur ye poora bug ki wajah tha.
#
# `for path in $(find ...)` ka natija whitespace pe TOOTTA hai. App ka folder
# hai "…/Employee-Tracking-System-main copy" — usme space hai — to har raasta
# do tukdon me bat gaya: "…/Employee-Tracking-System-main" aur
# "copy/server/migrations/….sql". psql ne ek bhi file nahi kholi aur poori
# script "Permission denied" / "No such file or directory" ugalti rahi.
#
# Quoted glob space ko safely sambhalta hai, aur shell use naam se sorted hi
# deta hai — wahi kram jo migrate.js aur _migrate.js use karte hain, aur isi
# liye migrations pe date aur sequence number hote hain.
#
# `mapfile` NAHI: wo bash 4 ka hai, aur macOS pe abhi bhi bash 3.2 hai — wahan
# ye script "command not found" (exit 127) deti hai. Ek nangi glob loop har
# jagah chalti hai.
shopt -s nullglob

for path in "$MIGRATIONS_DIR"/*.sql; do
    name="$(basename "$path")"

    # `._` se shuru hone wali files CHHODI jaati hain. macOS har us file ke
    # saath ek AppleDouble chhod deta hai jo Mac se copy hui ho; wo binary
    # hoti hain aur psql unpe "invalid message format" deta hai.
    case "$name" in ._*) continue ;; esac

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
    # STDIN, `-f` NAHI — aur ye zaroori hai.
    #
    # `-f` ke saath file ko PSQL kholta hai, aur psql yahan `postgres` user ke
    # roop me chal raha hai. Wo /home/etsadmin/... ke andar jhaank hi nahi
    # sakta, to har migration "Permission denied" deti hai — file par, database
    # par nahi. Poora output aisa lagta hai jaise database ne mana kiya ho,
    # jabki file kholi hi nahi gayi.
    #
    # `< "$path"` ka redirect wo shell karta hai jo etsadmin ka hai aur file
    # padh sakta hai; psql sirf stdin padhta hai. Isse file ki permission ka
    # sawaal hi khatam ho jaata hai.
    #
    # EK TRANSACTION ME — par -1 SIRF TAB jab file khud apni na sambhalti ho.
    #
    # 19 migrations apna BEGIN/COMMIT khud likhti hain. Un par -1 lagane se
    # do transaction ek dusre pe chadh jaate hain: psql ka BEGIN chalta hai,
    # file ka BEGIN "there is already a transaction in progress" warn karta
    # hai, file ka COMMIT bahar wale ko band kar deta hai, aur psql ka apna
    # COMMIT "there is no transaction in progress" par aakar khatam hota hai.
    #
    # Kaam ho jaata hai, par wo guarantee nahi rehti jo yahan likhi thi —
    # file ke COMMIT ke baad ka koi bhi statement transaction ke BAHAR chalta
    # hai. Jo file khud sambhalti hai, use apna kaam karne dena chahiye.
    # Do alag call, ek array nahi: `set -u` ke saath bash 3.2 me khaali array
    # "unbound variable" deta hai, aur wo bilkul unhi files pe lagta hai jo
    # apni transaction khud sambhalti hain — yaani theek us case pe jiske
    # liye ye likha gaya tha.
    if grep -qiE '^[[:space:]]*BEGIN[[:space:]]*;' "$path"; then
        run_it() { psql_super -q < "$path" > /dev/null; }
    else
        run_it() { psql_super -1 -q < "$path" > /dev/null; }
    fi

    if run_it \
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
