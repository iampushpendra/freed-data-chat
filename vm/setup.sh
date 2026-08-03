#!/bin/bash
# Idempotent provisioning for the Freed Data Chat query API.
# Run ON THE VM as the 'ubuntu' user: bash setup.sh
# (sudo is invoked internally for the steps that need it — root ownership of
# /etc/freed/chat-query.env, the systemd unit, and the nginx vhost.)
#
# Assumes query_api.py has already been copied to
# /home/ubuntu/freed-data-chat/query_api.py (scripts/deploy_vm.sh from the
# Mac does that scp before invoking this script).
set -euo pipefail

APP_DIR=/home/ubuntu/freed-data-chat
ENV_FILE=/etc/freed/chat-query.env
SETTLEMENT_ENV=/etc/freed/settlement.env
UNIT_NAME=freed-chat-query.service

echo "=== Freed Data Chat query API — VM setup ==="

echo "--- 1. App directory ---"
mkdir -p "$APP_DIR"
if [ ! -f "$APP_DIR/query_api.py" ]; then
  echo "ERROR: $APP_DIR/query_api.py not found. Copy it there first (see scripts/deploy_vm.sh)." >&2
  exit 1
fi
echo "found $APP_DIR/query_api.py"

echo "--- 2. /etc/freed/chat-query.env ---"
sudo mkdir -p /etc/freed
if [ -f "$ENV_FILE" ]; then
  echo "$ENV_FILE already exists — leaving it untouched (never overwrite)"
else
  if [ ! -f "$SETTLEMENT_ENV" ]; then
    echo "ERROR: $SETTLEMENT_ENV not found — cannot seed PG_* values." >&2
    exit 1
  fi

  # Pull PG_* values out of settlement.env (same warehouse, same read-only
  # BI user) and generate a fresh bearer secret. sudo is needed to read
  # settlement.env since it's 600 root:root.
  PG_HOST=$(sudo grep -E '^PG_HOST=' "$SETTLEMENT_ENV" | head -n1 | cut -d= -f2-)
  PG_PORT=$(sudo grep -E '^PG_PORT=' "$SETTLEMENT_ENV" | head -n1 | cut -d= -f2-)
  PG_DB=$(sudo grep -E '^PG_DB=' "$SETTLEMENT_ENV" | head -n1 | cut -d= -f2-)
  PG_USER=$(sudo grep -E '^PG_USER=' "$SETTLEMENT_ENV" | head -n1 | cut -d= -f2-)
  PG_PASSWORD=$(sudo grep -E '^PG_PASSWORD=' "$SETTLEMENT_ENV" | head -n1 | cut -d= -f2-)

  if [ -z "$PG_HOST" ] || [ -z "$PG_DB" ] || [ -z "$PG_USER" ] || [ -z "$PG_PASSWORD" ]; then
    echo "ERROR: one or more PG_* values missing/empty in $SETTLEMENT_ENV." >&2
    exit 1
  fi
  PG_PORT=${PG_PORT:-5432}

  QUERY_API_SECRET=$(openssl rand -hex 32)

  TMP_ENV=$(mktemp)
  cat > "$TMP_ENV" <<ENV
PG_HOST=$PG_HOST
PG_PORT=$PG_PORT
PG_DB=$PG_DB
PG_USER=$PG_USER
PG_PASSWORD=$PG_PASSWORD
QUERY_API_SECRET=$QUERY_API_SECRET
ROW_CAP=2000
STATEMENT_TIMEOUT=60s
BIND_HOST=127.0.0.1
BIND_PORT=8158
ENV

  sudo mv "$TMP_ENV" "$ENV_FILE"
  echo "created $ENV_FILE (seeded PG_* from settlement.env, generated a new QUERY_API_SECRET)"
fi

echo "--- 3. Lock down env file perms ---"
sudo chown root:root "$ENV_FILE"
sudo chmod 600 "$ENV_FILE"
echo "$ENV_FILE is now 600 root:root"

echo "--- 4. systemd unit ---"
sudo cp "$APP_DIR/vm/freed-chat-query.service" "/etc/systemd/system/$UNIT_NAME"
sudo systemctl daemon-reload
sudo systemctl enable "$UNIT_NAME"
sudo systemctl restart "$UNIT_NAME"
echo "$UNIT_NAME installed, enabled, and (re)started"

echo "--- 5. nginx vhost ---"
sudo cp "$APP_DIR/vm/nginx-chatquery.conf" /etc/nginx/sites-available/chatquery
sudo ln -sfn /etc/nginx/sites-available/chatquery /etc/nginx/sites-enabled/chatquery.conf
sudo nginx -t
sudo systemctl reload nginx
echo "nginx vhost installed and reloaded"

echo "--- 6. Smoke checks ---"
echo "listener on 8158:"
ss -ltn | grep 8158 || echo "WARNING: nothing listening on 8158"

echo "healthz via 127.0.0.1:8158:"
curl -sS -m 5 http://127.0.0.1:8158/healthz || echo "WARNING: healthz check on 8158 failed"
echo ""

echo "healthz via public :8157:"
curl -sS -m 5 http://127.0.0.1:8157/healthz || echo "WARNING: healthz check on 8157 failed"
echo ""

echo ""
echo "=== Setup complete ==="
if grep -q '^QUERY_API_SECRET=' "$ENV_FILE" 2>/dev/null; then
  :
fi
CURRENT_SECRET=$(sudo grep -E '^QUERY_API_SECRET=' "$ENV_FILE" | head -n1 | cut -d= -f2-)
echo "QUERY_API_SECRET (copy this into Vercel env as QUERY_API_SECRET, shown once here):"
echo "  $CURRENT_SECRET"
echo ""
echo "If this is a fresh env file, do this now — it will not be printed again by this script."
