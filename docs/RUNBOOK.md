# Analyst — Live Runbook

**Live site:** https://freed-data-chat.vercel.app
**Shared password:** set in Vercel env `CHAT_PASSWORD` (ask an admin; rotate via the steps below)

Ask plain-English data questions → Analyst writes read-only PostgreSQL against
the warehouse (`freed_app_db`), runs it, and returns a table + summary.

## Architecture (as deployed)

```
Browser ──password cookie──> Vercel  (project: freed-data-chat, team <vercel-team>)
  web/index.html                /api/login, /api/chat (OpenAI tool loop)
                                     │  run_sql tool, Bearer secret
                                     ▼
                          Cloudflare Tunnel  (dials OUT from the VM; no inbound port)
                                     ▼
                          VM <vm-host>  freed-chat-query.service  (127.0.0.1:8158)
                                SELECT/WITH-only validator · read-only PG · 60s · 2000 rows
                                     ▼
                          Postgres warehouse <warehouse-host> / freed_app_db
```

Why the tunnel: the VM's public IP does **not** accept inbound internet traffic
(the `<vpc-name>` firewall admits only office/VPN ranges), so Vercel reaches
the VM through an outbound Cloudflare tunnel instead of an opened port.

## ⚠️ Current state: interim quick tunnel

The data path runs over a Cloudflare **quick tunnel** (`analyst-quicktunnel.service`
on the VM). It works today, but its URL (`*.trycloudflare.com`) is **ephemeral** —
if that service restarts, the URL changes and `QUERY_API_URL` in Vercel must be
updated. Upgrade to a stable named tunnel when convenient:

1. One interactive step (opens a browser):
   ```bash
   cloudflared tunnel login
   ```
2. On the VM:
   ```bash
   bash /home/ubuntu/freed-data-chat/vm/tunnel_setup.sh analyst-query.<a-cloudflare-zone>
   ```
   (freed.care is on Route 53 — delegate the subdomain to Cloudflare first, or use
   a domain already on Cloudflare. See `agent-dashboard/docs/PORTAL.md` for the
   delegation pattern.)
3. Point Vercel at the stable host (the script prints these exact commands).

To read the current interim URL: `ssh …@<vm-host> 'grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" ~/freed-data-chat/quicktunnel.log | head -1'`

## Everyday ops

**Change the shared password**
```bash
cd ~/projects/freed-data-chat
vercel env rm CHAT_PASSWORD production -y
printf '%s' 'NEW_PASSWORD' | vercel env add CHAT_PASSWORD production
vercel --prod --yes
```

**Use a dedicated OpenAI key** (currently reuses the company key from the VM's
Note-Analyser tool): same pattern, env var `OPENAI_API_KEY`. Model is `OPENAI_MODEL` (gpt-4o).

**Rotate the VM bearer secret:** edit `QUERY_API_SECRET` in `/etc/freed/chat-query.env`
on the VM (`sudo`), `sudo systemctl restart freed-chat-query`, then update the same
value in Vercel env and redeploy.

**Logs**
- Query audit (one JSON line per request): VM `~/freed-data-chat/chat-query.log`
- Query service: `journalctl -u freed-chat-query`
- Tunnel: `journalctl -u analyst-quicktunnel` (or `cloudflared` once named)
- Vercel functions: `vercel logs freed-data-chat.vercel.app`

**Widen what Analyst knows:** edit `context/schema.md` (the LLM's schema + glossary),
commit, `vercel --prod --yes`. No VM change needed.

## Limits
- 3 SQL queries per message, 60s budget, 2000 rows per query (CSV download is full).
- Warehouse (Postgres) only in v1; Mongo sources are not exposed.
- Read-only end to end: SELECT/WITH validator + read-only PG user; writes are impossible.

## Tests
- Validator unit tests: `python3 vm/test_query_api.py` (90 tests).
- VM API: `QUERY_API_URL=<tunnel>/query QUERY_API_SECRET=… bash test/test_vm_query.sh`
- End to end: `CHAT_PASSWORD=… bash test/e2e.sh https://freed-data-chat.vercel.app`
