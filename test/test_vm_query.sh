#!/bin/bash
# Direct smoke test of the VM query API (vm/query_api.py), bypassing Vercel.
#
# Requires QUERY_API_URL (e.g. http://34.100.177.202:8157/query) and
# QUERY_API_SECRET exported.
#
# Prints PASS/FAIL per step; exits non-zero if any step fails.

set -uo pipefail

: "${QUERY_API_URL:?QUERY_API_URL must be exported (e.g. http://34.100.177.202:8157/query)}"
: "${QUERY_API_SECRET:?QUERY_API_SECRET must be exported}"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

have_jq() { command -v jq >/dev/null 2>&1; }

json_get() {
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

echo "=== VM query API test — $QUERY_API_URL ==="

post() {
  # $1 = sql, $2 = "auth"|"noauth" -> prints "CODE\nBODY"
  local sql="$1" auth_mode="${2:-auth}" body
  body="$(python3 -c 'import json,sys; print(json.dumps({"sql": sys.argv[1]}))' "$sql")"
  if [ "$auth_mode" = "auth" ]; then
    curl -sS -m 70 -o /tmp/vmq_resp.$$ -w '%{http_code}' \
      -X POST "$QUERY_API_URL" \
      -H "Authorization: Bearer $QUERY_API_SECRET" \
      -H 'Content-Type: application/json' \
      -d "$body"
  else
    curl -sS -m 70 -o /tmp/vmq_resp.$$ -w '%{http_code}' \
      -X POST "$QUERY_API_URL" \
      -H 'Content-Type: application/json' \
      -d "$body"
  fi
  echo ""
  cat /tmp/vmq_resp.$$ 2>/dev/null
  rm -f /tmp/vmq_resp.$$
}

# 1. select 1 as x -> 200, x=1
out="$(post "select 1 as x" auth)"
code="$(echo "$out" | head -n1)"
resp_body="$(echo "$out" | tail -n +2)"
x="$(json_get "$resp_body" '.rows[0][0]')"
if [ "$code" = "200" ] && [ "$x" = "1" ]; then
  pass "1. select 1 as x -> 200, x=1"
else
  fail "1. select 1 as x -> code=$code body=$resp_body"
fi

# 2. drop table foo -> 400 (rejected by validator)
out="$(post "drop table foo" auth)"
code="$(echo "$out" | head -n1)"
resp_body="$(echo "$out" | tail -n +2)"
if [ "$code" = "400" ]; then
  pass "2. drop table foo -> 400"
else
  fail "2. drop table foo -> expected 400, got code=$code body=$resp_body"
fi

# 3. select * from nonexistent_xyz -> 422 (ran, postgres error)
out="$(post "select * from nonexistent_xyz" auth)"
code="$(echo "$out" | head -n1)"
resp_body="$(echo "$out" | tail -n +2)"
if [ "$code" = "422" ]; then
  pass "3. select * from nonexistent_xyz -> 422"
else
  fail "3. select * from nonexistent_xyz -> expected 422, got code=$code body=$resp_body"
fi

# 4. no auth -> 401
out="$(post "select 1 as x" noauth)"
code="$(echo "$out" | head -n1)"
resp_body="$(echo "$out" | tail -n +2)"
if [ "$code" = "401" ]; then
  pass "4. no auth -> 401"
else
  fail "4. no auth -> expected 401, got code=$code body=$resp_body"
fi

echo ""
echo "=== $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
