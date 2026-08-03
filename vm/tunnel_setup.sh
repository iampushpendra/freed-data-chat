#!/bin/bash
# Upgrade the Analyst query-API bridge from the interim quick tunnel to a
# STABLE named Cloudflare tunnel. Run ON THE VM as ubuntu, AFTER you have run
# the one interactive step from your own laptop or the VM:
#
#     cloudflared tunnel login
#
# (that opens a browser, authorizes a Cloudflare account, and drops
#  ~/.cloudflared/cert.pem — it cannot be done headless).
#
# Usage:  bash tunnel_setup.sh <public-hostname>
#   e.g.  bash tunnel_setup.sh analyst-query.freed.care
#
# The hostname MUST live in a zone Cloudflare manages. freed.care is on AWS
# Route 53 (see agent-dashboard/docs/PORTAL.md), so either delegate the single
# subdomain to Cloudflare first (recommended, reversible) or use a subdomain of
# a domain already on Cloudflare. Without that, `tunnel route dns` below fails.
set -euo pipefail

HOSTNAME="${1:?usage: tunnel_setup.sh <public-hostname>}"
TUNNEL_NAME="analyst-query"
CF_DIR="$HOME/.cloudflared"

command -v cloudflared >/dev/null || { echo "cloudflared not installed"; exit 1; }
[ -f "$CF_DIR/cert.pem" ] || { echo "No $CF_DIR/cert.pem — run 'cloudflared tunnel login' first."; exit 1; }

echo "--- 1. Create tunnel (idempotent) ---"
if ! cloudflared tunnel list | grep -q " $TUNNEL_NAME "; then
  cloudflared tunnel create "$TUNNEL_NAME"
fi
UUID=$(cloudflared tunnel list | awk -v n="$TUNNEL_NAME" '$2==n{print $1}')
echo "tunnel UUID: $UUID"

echo "--- 2. Write config ---"
cat > "$CF_DIR/config.yml" <<CFG
tunnel: $UUID
credentials-file: $CF_DIR/$UUID.json
ingress:
  - hostname: $HOSTNAME
    service: http://127.0.0.1:8158
  - service: http_status:404
CFG

echo "--- 3. Route DNS (needs the zone on Cloudflare) ---"
cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME"

echo "--- 4. Install as a service, stop the interim quick tunnel ---"
sudo cloudflared --config "$CF_DIR/config.yml" service install || true
sudo systemctl enable --now cloudflared || true
sudo systemctl disable --now analyst-quicktunnel.service 2>/dev/null || true

echo "--- 5. Verify ---"
sleep 4
curl -fsS "https://$HOSTNAME/healthz" && echo
echo ""
echo "=== Done. Now set Vercel and redeploy (from the Mac repo): ==="
echo "  vercel env rm QUERY_API_URL production -y"
echo "  printf '%s' 'https://$HOSTNAME/query' | vercel env add QUERY_API_URL production"
echo "  vercel --prod --yes"
