#!/bin/bash
# Run ON THE MAC. Copies the query API + provisioning assets to the VM,
# runs setup.sh there (which installs/restarts the service and nginx
# vhost), and verifies healthz from outside the VM.
set -euo pipefail

SSH_KEY="$HOME/installs/keys/freed-live-gcp.pem"
VM_HOST="ubuntu@172.20.4.15"
VM_PUBLIC_IP="34.100.177.202"
REMOTE_DIR="/home/ubuntu/freed-data-chat"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=accept-new)

echo "=== Freed Data Chat — deploy query API to VM ==="

echo "--- 1. Ensure remote directory exists ---"
ssh "${SSH_OPTS[@]}" "$VM_HOST" "mkdir -p $REMOTE_DIR/vm"

echo "--- 2. Copy files ---"
scp "${SSH_OPTS[@]}" \
  "$REPO_ROOT/vm/query_api.py" \
  "$REPO_ROOT/vm/setup.sh" \
  "$REPO_ROOT/vm/freed-chat-query.service" \
  "$REPO_ROOT/vm/nginx-chatquery.conf" \
  "$VM_HOST:$REMOTE_DIR/vm/"

# setup.sh and query_api.py are read from $REMOTE_DIR/vm/... by the remote
# setup script's own relative paths (APP_DIR/vm/...), and query_api.py is
# also expected directly at $REMOTE_DIR/query_api.py — copy it up one level
# too so `ExecStart=/usr/bin/python3 /home/ubuntu/freed-data-chat/query_api.py`
# in the systemd unit finds it.
scp "${SSH_OPTS[@]}" "$REPO_ROOT/vm/query_api.py" "$VM_HOST:$REMOTE_DIR/query_api.py"

echo "--- 3. Run setup.sh on the VM ---"
ssh "${SSH_OPTS[@]}" "$VM_HOST" "bash $REMOTE_DIR/vm/setup.sh"

echo "--- 4. Verify healthz from the Mac ---"
sleep 2
curl -sS -m 10 "http://$VM_PUBLIC_IP:8157/healthz"
echo ""

echo ""
echo "=== Deploy complete ==="
echo "Verified http://$VM_PUBLIC_IP:8157/healthz from the Mac."
