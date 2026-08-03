# Task 3 — VM provisioning assets

**Status:** Done.

## Deliverables

- `vm/freed-chat-query.service` — systemd unit, `User=ubuntu`,
  `EnvironmentFile=/etc/freed/chat-query.env`, `ExecStart=/usr/bin/python3
  /home/ubuntu/freed-data-chat/query_api.py`, `Restart=always`,
  `RestartSec=5`, `WorkingDirectory=/home/ubuntu/freed-data-chat`,
  hardening: `NoNewPrivileges`, `ProtectSystem=full`, `PrivateTmp`.
- `vm/nginx-chatquery.conf` — `listen 8157;` (all interfaces), `server_name
  _;`, header comment block explaining the deliberate public bind (bearer
  auth is the real boundary, matches call-analysis vhost pattern, not
  callhistory's private-bind pattern) plus a TLS-upgrade path note. Proxies
  `/` and `/healthz` to `http://127.0.0.1:8158`, `Cache-Control: no-store`,
  `X-Content-Type-Options: nosniff`, `client_max_body_size 1m`, connect
  timeout 10s, send/read timeout 90s, access/error logs at
  `/var/log/nginx/chatquery.{access,error}.log`. Install-instruction comment
  matches `agent-dashboard/scripts/vm/nginx-callhistory.conf` style,
  including the "symlink must end in .conf" warning.
- `vm/setup.sh` — idempotent, `set -euo pipefail`, run on the VM as ubuntu.
  Creates `/home/ubuntu/freed-data-chat`, fails loudly if `query_api.py`
  isn't already there. Creates `/etc/freed/chat-query.env` only if missing
  (never overwrites), seeding `PG_HOST/PG_PORT/PG_DB/PG_USER/PG_PASSWORD`
  from `/etc/freed/settlement.env` (same var names confirmed in
  `agent-dashboard/scripts/fetch_calls_postgres.py`) and generating
  `QUERY_API_SECRET` via `openssl rand -hex 32`; also sets `ROW_CAP=2000`,
  `STATEMENT_TIMEOUT=60s`, `BIND_HOST=127.0.0.1`, `BIND_PORT=8158` per the
  contract's documented env var list. Locks the file to `600 root:root`,
  installs+enables+restarts the systemd unit, installs the nginx vhost
  (sites-available + `.conf` symlink), runs `nginx -t && reload`, then smoke
  checks (`ss -ltn | grep 8158`, curl healthz on both 127.0.0.1:8158 and
  127.0.0.1:8157) and prints `QUERY_API_SECRET` with a "copy this into
  Vercel env" note.
- `scripts/deploy_vm.sh` — run on the Mac, `set -euo pipefail`. mkdir -p on
  the remote dir, scp's `query_api.py`, `setup.sh`,
  `freed-chat-query.service`, `nginx-chatquery.conf` into
  `/home/ubuntu/freed-data-chat/vm/` (and `query_api.py` also directly into
  `/home/ubuntu/freed-data-chat/` since the systemd unit's `ExecStart` and
  `setup.sh`'s existence check both expect it one level up from `vm/`), runs
  `setup.sh` over ssh, `sudo systemctl restart freed-chat-query`, then curls
  `http://34.100.177.202:8157/healthz` from the Mac to confirm the public
  path works end-to-end. Uses key `~/installs/keys/freed-live-gcp.pem`.

## Checks done

- Read `docs/CONTRACTS.md` and `docs/DESIGN.md` for the binding env var
  names, ports (127.0.0.1:8158 internal, 0.0.0.0:8157 public), and
  systemd/nginx pattern ("same pattern as callhistory... but PUBLIC bind +
  bearer auth, like the call-analysis vhosts").
  - Read `agent-dashboard/scripts/vm/nginx-callhistory.conf` and matched its
    header-comment structure, the install-instruction block, and the
    "symlink must end in .conf" gotcha.
- Cross-checked `agent-dashboard/scripts/vm/freed-dashboard.service` and
  `freed-settlement.service` for the `EnvironmentFile=/etc/freed/*.env` +
  `User=ubuntu` + `WorkingDirectory=/home/ubuntu/...` conventions used on
  this VM.
- Confirmed `PG_HOST/PG_PORT/PG_DB/PG_USER/PG_PASSWORD` are the exact var
  names already used in `settlement.env` via
  `agent-dashboard/scripts/fetch_calls_postgres.py`.
- `bash -n` passed clean on both `vm/setup.sh` and `scripts/deploy_vm.sh`.
- No `nginx` binary available locally, so `nginx -t` on the vhost config
  could not be run here — it's exercised inside `setup.sh` itself when run
  on the VM.

## Concerns / things worth a second look before running on the VM

- `setup.sh` assumes `settlement.env` lines are plain `KEY=value` with no
  quoting/spaces around `=`; if that file ever uses `export KEY=value` or
  quoted values, the `grep | cut` extraction will need adjusting.
- Per the task spec, the QUERY_API_SECRET print is treated as "print it for
  the operator to copy" rather than "print literally only on the very first
  ever run" — since `setup.sh` is idempotent and safe to re-run, it always
  reads and prints the current secret from the env file (via `sudo grep`) so
  a re-run to fix nginx/systemd doesn't strand the operator without the
  value. It does not print it if `/etc/freed/chat-query.env` and its
  extraction fail for some other reason before that point (script would have
  already exited nonzero).
- Did not create `/etc/freed/chat-query.env`, ssh to the VM, or run
  `setup.sh`/`deploy_vm.sh` for real, per the task's "do NOT ssh, do NOT
  deploy" instruction — everything above is static-reviewed and
  syntax-checked only.
- `vm/query_api.py` itself does not exist yet in this checkout (being
  written by a parallel agent); `setup.sh`'s existence check and
  `deploy_vm.sh`'s scp both assume it lands at `vm/query_api.py` in the repo
  before deploy is actually run.
