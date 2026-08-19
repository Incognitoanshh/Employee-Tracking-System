#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  scripts/migrate.sh ko asli migrations pe chalata hai — ek directory me
#  jiske naam me SPACE hai.
#
#  YE KYUN HAI. Wo script bina ek baar chalaye bheji gayi thi, aur production
#  pe hi tooti. Wajah: `for path in $(find ...)` whitespace pe tootta hai, aur
#  app ka folder hai ".../Employee-Tracking-System-main copy". Har raasta do
#  tukdon me bat gaya, psql ne ek bhi file nahi kholi, aur 45 "FAILED" lines
#  aayin jinme se ek bhi asli migration ki galti nahi thi.
#
#  Isliye ye test space ko sanyog pe nahi chhodta — directory ka naam JAAN
#  BOOJH KAR space ke saath banaya jaata hai. Wahi ek cheez us bug ko pakadti
#  hai, aur usi ek cheez ki kami thi.
#
#  Chalane ka tarika:  bash server/tests/test_migrate_sh.sh
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DB="ets_shtest_$$"
# SPACE, on purpose. See above.
WORK="$(mktemp -d)/app copy"
failures=0

check() {
    if [ "$2" = "1" ]; then
        echo "  PASS  $1"
    else
        echo "  FAIL  $1${3:+  — $3}"
        failures=$((failures + 1))
    fi
}

cleanup() {
    psql -d postgres -q -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null 2>&1
    rm -rf "$(dirname "$WORK")"
}
trap cleanup EXIT

echo "migrate.sh ($DB)"
echo

mkdir -p "$WORK/server/scripts" "$WORK/server/migrations"
cp "$ROOT/server/scripts/migrate.sh" "$WORK/server/scripts/"
cp "$ROOT/server/migrations/"*.sql "$WORK/server/migrations/"
# A .env SHAPED LIKE THE REAL ONE, which is to say with values that would
# break a shell if the file were sourced. The mail settings did exactly that
# the evening they were added:
#
#     ./.env: line 15: qlqm: command not found
#     ./.env: line 16: syntax error near unexpected token `newline'
#
# backup.sh and migrate.sh both stopped working, on a file whose only change
# was an unrelated setting. So the test writes those values in.
{
    printf 'DB_NAME=%s\n' "$DB"
    printf 'SMTP_PASS=abcd efgh ijkl mnop\n'
    printf 'SMTP_FROM=Amaze Connect <no-reply@example.com>\n'
    printf 'JWT_SECRET=a secret with spaces & symbols $(not-a-command)\n'
} > "$WORK/server/.env"

# Ek AppleDouble, waise hi jaise macOS chhodta hai — binary, aur .sql jaisa
# dikhta hai.
printf '\x00\x05\x16\x07\x00\x02\x00\x00' > "$WORK/server/migrations/._2026_08_01_1_admin_config.sql"

psql -d postgres -q -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null 2>&1
psql -d postgres -q -c "CREATE DATABASE $DB" >/dev/null
# Sirf base schema — migrations wahi hain jo script ko lagani hain.
psql -d "$DB" -q -v ON_ERROR_STOP=1 -f "$ROOT/ets.sql" >/dev/null 2>&1

echo "A .env whose values would break a shell that sourced it"
out="$(cd "$WORK/server" && MIGRATE_NO_SUDO=1 bash scripts/migrate.sh 2>&1)"
code=$?

echo "$out" | grep -qE "No such file or directory|Permission denied" && split=1 || split=0
check "koi raasta space pe tootta nahi" "$([ "$split" = "0" ] && echo 1 || echo 0)" \
      "$(echo "$out" | grep -E 'No such file|Permission denied' | head -2)"
check "script kaamyaab hoti hai" "$([ "$code" = "0" ] && echo 1 || echo 0)" \
      "exit $code"

applied="$(psql -d "$DB" -tAc 'SELECT count(*) FROM schema_migrations' 2>/dev/null | tr -d ' ')"
on_disk="$(ls "$ROOT/server/migrations/"*.sql | grep -v '/\._' | grep -vc 'retention_purge')"
check "har migration lagi aur record hui" \
      "$([ "$applied" = "$on_disk" ] && echo 1 || echo 0)" \
      "$applied recorded, $on_disk on disk"

check "AppleDouble ko migration nahi maana" \
      "$(psql -d "$DB" -tAc "SELECT count(*) FROM schema_migrations WHERE name LIKE '._%'" | grep -q '^0$' && echo 1 || echo 0)"

# Wo columns jinke liye aaj ye sab hua — schema me sach me aayi ya nahi.
for col in email email_verified_at phone department joining_date; do
    have="$(psql -d "$DB" -tAc "SELECT count(*) FROM information_schema.columns
                                WHERE table_name='employees' AND column_name='$col'")"
    check "employees.$col maujood hai" "$([ "$have" = "1" ] && echo 1 || echo 0)"
done

echo
echo "Dobara chalane par kuch nahi badalta"
out2="$(cd "$WORK/server" && MIGRATE_NO_SUDO=1 bash scripts/migrate.sh 2>&1)"
check "sab skip hoti hain" \
      "$(echo "$out2" | grep -q "^0 lagayi, $on_disk pehle se thi, 0 fail." && echo 1 || echo 0)" \
      "$(echo "$out2" | tail -3 | head -1)"

echo
echo "File PSQL nahi kholta — kyunki wo postgres ban kar chalta hai"
# YE FUNCTIONAL TEST SE PAKDA NAHI JA SAKTA. Yahan sab kuch EK hi user ke
# roop me chalta hai, jo har file padh sakta hai. Server pe psql `postgres`
# banta hai aur $ETS_HOME/... ke andar jhaank nahi sakta — to `-f` ke
# saath har migration "Permission denied" deti hai, file par, database par
# nahi. Poora output aisa lagta hai jaise database ne mana kiya ho.
#
# Isliye ye baat khud script ke code pe jaanchi jaati hai: file wahi shell
# padhe jo use padh sakta hai, aur psql ko sirf stdin mile.
check "migration -f se nahi, stdin se chalti hai" \
      "$(grep -q 'psql_super -1 -q < "\$path"' "$ROOT/server/scripts/migrate.sh" && echo 1 || echo 0)" \
      "$(grep -n 'psql_super -1' "$ROOT/server/scripts/migrate.sh")"
check "kisi bhi migration ko -f se nahi khola jaata" \
      "$(grep -q '\-f "\$path"' "$ROOT/server/scripts/migrate.sh" && echo 0 || echo 1)"

echo
echo "Jaisa production HAI: sab lagi hui, record me kuch nahi"
# YAHI ASLI HAALAT HAI. Production pe har migration mahino se lagi hui hai,
# par schema_migrations wahan tab bana jab boot runner pehli baar chala — to
# usme sirf woh 8 hain jo us raat lag payin. Baaki 18 dobara chalengi.
#
# Iska matlab hai ki HAR migration ka dobara chalna surakshit hona chahiye.
# Agar koi ek bhi nahi hai, wahi 45-FAILED wali screen phir aayegi — is baar
# asli galti ke saath. Isliye ye yahan jaancha jaata hai, na ki server pe.
psql -d "$DB" -q -c "DELETE FROM schema_migrations" >/dev/null
out3="$(cd "$WORK/server" && MIGRATE_NO_SUDO=1 bash scripts/migrate.sh 2>&1)"
code3=$?
check "har migration dobara chal jaati hai" "$([ "$code3" = "0" ] && echo 1 || echo 0)" \
      "$(echo "$out3" | grep -A1 FAILED | head -4)"
check "aur sab dobara record ho jaati hain" \
      "$([ "$(psql -d "$DB" -tAc 'SELECT count(*) FROM schema_migrations')" = "$on_disk" ] && echo 1 || echo 0)"

echo
echo "--dry-run kuch badalta nahi"
before="$(psql -d "$DB" -tAc 'SELECT count(*) FROM schema_migrations')"
(cd "$WORK/server" && MIGRATE_NO_SUDO=1 bash scripts/migrate.sh --dry-run >/dev/null 2>&1)
after="$(psql -d "$DB" -tAc 'SELECT count(*) FROM schema_migrations')"
check "record waisa hi rehta hai" "$([ "$before" = "$after" ] && echo 1 || echo 0)" \
      "$before → $after"

echo
if [ "$failures" -gt 0 ]; then
    echo "$failures failure(s)"
    exit 1
fi
echo "all migrate.sh checks passed"
