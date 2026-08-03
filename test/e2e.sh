#!/bin/bash
# End-to-end smoke test against a deployed freed-data-chat Vercel instance.
#
# Usage: test/e2e.sh [BASE_URL]
#   BASE_URL   defaults to $1, else https://freed-data-chat.vercel.app
#   CHAT_PASSWORD  must be exported (the correct shared password)
#   QUERY_API_URL  optional; if exported, step 0 hits the VM's /healthz directly
#
# Prints PASS/FAIL per step; exits non-zero if any step fails.

set -uo pipefail

BASE_URL="${1:-https://freed-data-chat.vercel.app}"
CHAT_PASSWORD="${CHAT_PASSWORD:-}"

PASS=0
FAIL=0
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

have_jq() { command -v jq >/dev/null 2>&1; }

json_get() {
  # $1 = json string, $2 = jq filter (e.g. '.reply')
  local json="$1" filter="$2"
  if have_jq; then
    printf '%s' "$json" | jq -r "$filter" 2>/dev/null
  else
    python3 -c "
import json, sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    print('')
    sys.exit(0)
f = sys.argv[2].lstrip('.')
cur = d
for part in f.split('.'):
    if part == '':
        continue
    # support foo[0] style
    import re
    m = re.match(r'([^\[]+)(\[(\d+)\])?', part)
    key, idx = m.group(1), m.group(3)
    if key:
        cur = cur.get(key) if isinstance(cur, dict) else None
    if idx is not None and cur is not None:
        try:
            cur = cur[int(idx)]
        except Exception:
            cur = None
print('' if cur is None else cur)
" "$json" "$filter"
  fi
}

echo "=== Freed Data Chat E2E — $BASE_URL ==="

# Step 0: VM healthz (only if QUERY_API_URL is exported)
if [ -n "${QUERY_API_URL:-}" ]; then
  HEALTHZ_URL="${QUERY_API_URL%/query}/healthz"
  body="$(curl -sS -m 10 "$HEALTHZ_URL" || true)"
  ok="$(json_get "$body" '.ok')"
  if [ "$ok" = "true" ]; then
    pass "0. VM healthz ($HEALTHZ_URL) -> ok:true"
  else
    fail "0. VM healthz ($HEALTHZ_URL) -> $body"
  fi
else
  echo "SKIP: 0. VM healthz (QUERY_API_URL not exported)"
fi

if [ -z "$CHAT_PASSWORD" ]; then
  echo "ERROR: CHAT_PASSWORD must be exported to run steps 1-4." >&2
  exit 1
fi

# Step 1: wrong password -> 401
code="$(curl -sS -o /dev/null -w '%{http_code}' -m 20 \
  -X POST "$BASE_URL/api/login" \
  -H 'Content-Type: application/json' \
  -d '{"password":"definitely-wrong-password-xyz"}')"
if [ "$code" = "401" ]; then
  pass "1. wrong password -> 401"
else
  fail "1. wrong password -> expected 401, got $code"
fi

# Step 2: right password -> capture cookie
code="$(curl -sS -o /dev/null -w '%{http_code}' -m 20 -c "$COOKIE_JAR" \
  -X POST "$BASE_URL/api/login" \
  -H 'Content-Type: application/json' \
  -d "{\"password\":\"$CHAT_PASSWORD\"}")"
if [ "$code" = "200" ] && grep -q fdc_auth "$COOKIE_JAR" 2>/dev/null; then
  pass "2. right password -> 200 + fdc_auth cookie set"
else
  fail "2. right password -> expected 200 + cookie, got code=$code, cookie_jar=$(cat "$COOKIE_JAR" 2>/dev/null)"
fi

# Step 3: chat without cookie -> 401
code="$(curl -sS -o /dev/null -w '%{http_code}' -m 20 \
  -X POST "$BASE_URL/api/chat" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"hello"}]}')"
if [ "$code" = "401" ]; then
  pass "3. chat without cookie -> 401"
else
  fail "3. chat without cookie -> expected 401, got $code"
fi

# Step 4: chat with cookie -> 200, non-empty reply, tables[0].row_count>=1
resp="$(curl -sS -m 65 -b "$COOKIE_JAR" \
  -o /tmp/e2e_chat_resp.$$ -w '%{http_code}' \
  -X POST "$BASE_URL/api/chat" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"How many leads signed up in the last 7 days? Give a single number."}]}')"
body="$(cat /tmp/e2e_chat_resp.$$ 2>/dev/null)"
rm -f /tmp/e2e_chat_resp.$$

code="$resp"
reply="$(json_get "$body" '.reply')"
row_count="$(json_get "$body" '.tables[0].row_count')"

if [ "$code" = "200" ] && [ -n "$reply" ] && [ "$row_count" -ge 1 ] 2>/dev/null; then
  pass "4. chat with cookie -> 200, non-empty reply, tables[0].row_count=$row_count"
else
  fail "4. chat with cookie -> code=$code reply='${reply:0:120}' row_count='$row_count'"
fi

echo ""
echo "=== $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
