#!/bin/bash

BASE_URL="http://65.21.212.85:8000/api"
ADMIN_ID="admin"
ADMIN_PASS="admin123"
ADMIN_EMP_ID="EMP001"
EMP_ID="EMP002"
EMP_PASS="ansh"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

pass() { echo -e "${GREEN}[PASS]${NC} $1"; ((PASS++)); }
fail() { echo -e "${RED}[FAIL]${NC} $1"; ((FAIL++)); }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; ((WARN++)); }
section() { echo -e "\n${CYAN}=== $1 ===${NC}"; }

check() {
    local label="$1"
    local expected="$2"
    local response="$3"
    local success
    success=$(echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('success',''))" 2>/dev/null)
    if [ "$success" = "$expected" ]; then
        pass "$label"
    else
        fail "$label | response: $(echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('message','?'))" 2>/dev/null)"
    fi
}

# ── 1. AUTH ──────────────────────────────────────────────────────────────────
section "1. AUTH"

# Admin login
R=$(curl -s -X POST "$BASE_URL/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$ADMIN_ID\",\"password\":\"$ADMIN_PASS\"}")
ADMIN_TOKEN=$(echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null)
if [ -n "$ADMIN_TOKEN" ]; then pass "Admin login"; else fail "Admin login | $R"; fi

# Employee login
R=$(curl -s -X POST "$BASE_URL/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$EMP_ID\",\"password\":\"$EMP_PASS\"}")
EMP_TOKEN=$(echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null)
if [ -n "$EMP_TOKEN" ]; then pass "Employee login"; else warn "Employee login failed (EMP002 may not exist) | $R"; fi

# Invalid login (should fail)
R=$(curl -s -X POST "$BASE_URL/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"INVALID","password":"wrong"}')
check "Invalid login → false" "False" "$R"

# No token (should 401)
R=$(curl -s "$BASE_URL/dashboard/stats")
MSG=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message',''))" 2>/dev/null)
if echo "$MSG" | grep -qi "token\|denied\|access"; then pass "No token → 401"; else fail "No token → 401 | $R"; fi

# Invalid token (should 403)
R=$(curl -s "$BASE_URL/dashboard/stats" -H "Authorization: Bearer invalidtoken123")
MSG=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message',''))" 2>/dev/null)
if echo "$MSG" | grep -qi "invalid\|expired\|token"; then pass "Invalid token → 403"; else fail "Invalid token → 403 | $R"; fi

# ── 2. STATUS / PING ─────────────────────────────────────────────────────────
section "2. STATUS / PING"

R=$(curl -s "$BASE_URL/status/ping")
if echo "$R" | grep -qi "pong\|ok\|alive\|success"; then pass "GET /status/ping"; else warn "GET /status/ping → $R (route may not exist)"; fi

# ── 3. DASHBOARD ─────────────────────────────────────────────────────────────
section "3. DASHBOARD"

R=$(curl -s "$BASE_URL/dashboard/stats" -H "Authorization: Bearer $ADMIN_TOKEN")
check "GET /dashboard/stats (admin)" "True" "$R"

R=$(curl -s "$BASE_URL/dashboard/summary" -H "Authorization: Bearer $ADMIN_TOKEN")
check "GET /dashboard/summary (admin)" "True" "$R"

R=$(curl -s "$BASE_URL/dashboard/recent-activity" -H "Authorization: Bearer $ADMIN_TOKEN")
check "GET /dashboard/recent-activity (admin)" "True" "$R"

R=$(curl -s "$BASE_URL/dashboard/charts" -H "Authorization: Bearer $ADMIN_TOKEN")
check "GET /dashboard/charts (admin)" "True" "$R"

# Non-admin hitting admin-only routes
if [ -n "$EMP_TOKEN" ]; then
    R=$(curl -s "$BASE_URL/dashboard/summary" -H "Authorization: Bearer $EMP_TOKEN")
    check "GET /dashboard/summary (employee → should fail)" "False" "$R"
fi

# ── 4. ATTENDANCE ────────────────────────────────────────────────────────────
section "4. ATTENDANCE"

R=$(curl -s "$BASE_URL/attendance/all" -H "Authorization: Bearer $ADMIN_TOKEN")
check "GET /attendance/all" "True" "$R"

R=$(curl -s -X POST "$BASE_URL/attendance/login" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"employee_id\":\"$ADMIN_EMP_ID\"}")
check "POST /attendance/login" "True" "$R"

R=$(curl -s -X POST "$BASE_URL/attendance/logout" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"employee_id\":\"$ADMIN_EMP_ID\"}")
check "POST /attendance/logout" "True" "$R"

# ── 5. LOGS ──────────────────────────────────────────────────────────────────
section "5. LOGS"

R=$(curl -s -X POST "$BASE_URL/logs/create" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"employee_id\":\"$ADMIN_EMP_ID\",\"activity\":\"TEST: deploy.sh automated test log\"}")
check "POST /logs/create" "True" "$R"

R=$(curl -s "$BASE_URL/logs/all" -H "Authorization: Bearer $ADMIN_TOKEN")
check "GET /logs/all" "True" "$R"

# ── 6. CONFIG ────────────────────────────────────────────────────────────────
section "6. CONFIG"

R=$(curl -s -X POST "$BASE_URL/config/sync" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"employee_id\":\"$ADMIN_EMP_ID\"}")
check "POST /config/sync" "True" "$R"

# ── 7. SCREENSHOTS ───────────────────────────────────────────────────────────
section "7. SCREENSHOTS"

R=$(curl -s "$BASE_URL/screenshots/all" -H "Authorization: Bearer $ADMIN_TOKEN")
check "GET /screenshots/all" "True" "$R"

# Upload a dummy encrypted screenshot
DUMMY_ENC=$(python3 -c "import os,base64; print(base64.b64encode(os.urandom(64)).decode())")
TMPFILE=$(mktemp /tmp/test_XXXXXX.enc)
echo "$DUMMY_ENC" > "$TMPFILE"
R=$(curl -s -X POST "$BASE_URL/screenshots/upload" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -F "screenshot=@$TMPFILE;type=application/octet-stream" \
    -F "employee_id=$ADMIN_EMP_ID" \
    -F "session_id=test-session-001")
rm -f "$TMPFILE"
check "POST /screenshots/upload" "True" "$R"
SS_ID=$(echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('id','') or d.get('id',''))" 2>/dev/null)

if [ -n "$SS_ID" ] && [ "$SS_ID" != "None" ]; then
    R=$(curl -s "$BASE_URL/screenshots/download/$SS_ID" -H "Authorization: Bearer $ADMIN_TOKEN")
    if echo "$R" | grep -qi "success\|true\|data"; then pass "GET /screenshots/download/:id"; else warn "GET /screenshots/download/$SS_ID → $R"; fi
else
    warn "Screenshot upload returned no ID — skipping download test"
fi

# Path traversal check
R=$(curl -s "$BASE_URL/screenshots/download/../../etc/passwd" -H "Authorization: Bearer $ADMIN_TOKEN")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if not d.get('success') else 1)" 2>/dev/null; then
    pass "Path traversal blocked"
else
    fail "Path traversal NOT blocked!"
fi

# ── 8. ADMIN ─────────────────────────────────────────────────────────────────
section "8. ADMIN"

R=$(curl -s "$BASE_URL/admin/employees" -H "Authorization: Bearer $ADMIN_TOKEN")
check "GET /admin/employees" "True" "$R"

R=$(curl -s "$BASE_URL/admin/employee/$ADMIN_ID" -H "Authorization: Bearer $ADMIN_TOKEN")
check "GET /admin/employee/:id" "True" "$R"

R=$(curl -s "$BASE_URL/admin/config/global" -H "Authorization: Bearer $ADMIN_TOKEN")
check "GET /admin/config/global" "True" "$R"

R=$(curl -s "$BASE_URL/admin/config/$ADMIN_ID" -H "Authorization: Bearer $ADMIN_TOKEN")
check "GET /admin/config/:employee_id" "True" "$R"

R=$(curl -s -X POST "$BASE_URL/admin/config" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"employee_id":"global","screenshot_min_minutes":5,"screenshot_max_minutes":10}')
check "POST /admin/config (valid)" "True" "$R"

R=$(curl -s -X POST "$BASE_URL/admin/config" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"employee_id":"global","screenshot_min_minutes":0,"screenshot_max_minutes":999}')
check "POST /admin/config (invalid → should fail)" "False" "$R"

R=$(curl -s -X POST "$BASE_URL/admin/force-logout" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$EMP_ID\"}")
check "POST /admin/force-logout" "True" "$R"

R=$(curl -s "$BASE_URL/admin/screenshots" -H "Authorization: Bearer $ADMIN_TOKEN")
check "GET /admin/screenshots" "True" "$R"

R=$(curl -s "$BASE_URL/admin/logs" -H "Authorization: Bearer $ADMIN_TOKEN")
check "GET /admin/logs" "True" "$R"

# Employee hitting admin routes (should fail)
if [ -n "$EMP_TOKEN" ]; then
    R=$(curl -s "$BASE_URL/admin/employees" -H "Authorization: Bearer $EMP_TOKEN")
    check "GET /admin/employees (employee → should fail)" "False" "$R"
fi

# ── SUMMARY ──────────────────────────────────────────────────────────────────
section "SUMMARY"
TOTAL=$((PASS + FAIL + WARN))
echo -e "${GREEN}PASS: $PASS${NC} | ${RED}FAIL: $FAIL${NC} | ${YELLOW}WARN: $WARN${NC} | Total: $TOTAL"
if [ $FAIL -eq 0 ]; then
    echo -e "${GREEN}All critical tests passed!${NC}"
else
    echo -e "${RED}$FAIL test(s) failed — check above.${NC}"
fi