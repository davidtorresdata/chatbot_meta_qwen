# Exposing only the webhook (HTTPS reverse proxy with Caddy)

The chatbot can stay on a private machine: you do **not** need to expose the
whole app, only the single `/webhook` endpoint that Meta uses, and only over
HTTPS. Caddy is used because it provisions a Let's Encrypt certificate
automatically and forwards requests to the FastAPI app on port 8000.

## Architecture

```
Meta (Cloud API)
   │  HTTPS, only:
   │    POST https://bot.example.com/webhook   (message payloads, signed)
   │    GET  https://bot.example.com/webhook   (verification handshake)
   ▼
Caddy (public ports 80/443 only)   ──►  whatsapp-qwen-chatbot:8000   (FastAPI, private)
```

- The chatbot binds to port 8000 and is **not** reachable from the internet —
  the host firewall allows inbound connections on 80/443 only (or 8080 in local
  mode).
- `GET /health` is also proxied, so monitoring works without opening 8000.

## Out of the box (local testing) — works with zero configuration

No domain, no DNS, no certificate. Caddy listens on `http://localhost:8080`
and forwards to the chatbot container. To start it:

```bash
docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d
```

Verify and run the full webhook test suite:

```bash
curl http://localhost:8080/health
powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1
```

The test script checks the health endpoint, the Meta verification handshake,
and signed/unsigned `POST /webhook` behaviour, all without touching Meta.

## Real domain via Cloudflare Tunnel (recommended, works behind NAT/CGNAT)

This machine has no reachable public IP (CGNAT), so Caddy cannot obtain a
Let's Encrypt certificate directly. A **Cloudflare Tunnel** gives the real
domain a working public HTTPS URL with zero router or firewall changes:
Cloudflare terminates HTTPS at its edge and connects back to Caddy running here
in local mode.

```
Meta (Cloud API)
   │  POST https://bot.fertrac.com/webhook   (HTTPS, signed)
   ▼
Cloudflare edge  (TLS cert for the hostname, auto-issued)
   │  outbound-only QUIC connection from this machine
   ▼
cloudflared ──► http://localhost:8080 ──► whatsapp-qwen-chatbot:8000 (FastAPI)
```

Only the connection *from this machine to Cloudflare* is outbound — no inbound
ports or port forwarding are needed.

Steps:

1. Keep Caddy in **local mode** (leave `CADDY_DOMAIN` unset in `.env`).
2. Create a free Cloudflare account and **add `fertrac.com` as a site**
   (dash.cloudflare.com → Add site). Cloudflare scans and imports the current
   DNS records.
3. In GoDaddy (Domain → DNS → Nameservers), replace GoDaddy's nameservers with
   the two Cloudflare nameservers shown when you add the site (e.g.
   `ns1.cloudflare.com` / `ns2.cloudflare.com`). Activation takes from minutes
   up to ~24 h. Cloudflare still proxies the existing records by default, so
   the current website keeps working until you re-point it.
4. Install cloudflared if not already present:
   `winget install --id Cloudflare.cloudflared`
   (this repo also finds it at `%LOCALAPPDATA%\cloudflared\cloudflared.exe`).
5. Create a named tunnel (Cloudflare dashboard → Zero Trust → Networks →
   Tunnels → Create a tunnel, connector type cloudflared), then authenticate
   and create it locally:
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create fertrac
   ```
6. Route the real hostname to the tunnel:
   ```bash
   cloudflared tunnel route dns fertrac bot.fertrac.com
   ```
   (use `fertrac.com` for the apex only if the existing GoDaddy site is
   retired — a subdomain like `bot.fertrac.com` leaves the current site alone).
7. Write `%USERPROFILE%\.cloudflared\config.yml`:
   ```yaml
   tunnel: fertrac
   credentials-file: C:\Users\<you>\.cloudflared\<tunnel-id>.json
   ingress:
     - hostname: bot.fertrac.com
       service: http://localhost:8080
     - service: http_status:404
   ```
8. Run the tunnel:
   ```bash
   cloudflared tunnel run fertrac
   ```
   To keep it always on as a Windows service:
   ```bash
   cloudflared service install
   ```
9. Cloudflare issues the TLS certificate for the hostname automatically
   (Universal SSL). Verify:
   ```bash
   curl https://bot.fertrac.com/health
   powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1 -BaseUrl https://bot.fertrac.com -SkipComposeUp
   ```
10. In the Meta dashboard, set **Callback URL:**
    `https://bot.fertrac.com/webhook`, verify token from `.env`, and subscribe
    to **messages**.

Notes:

- `scripts\tunnel.ps1` is a quick ad-hoc tunnel (random `trycloudflare.com`
  URL) for fast tests without any Cloudflare setup. The URL changes on every
  run, so use the named tunnel above for the real domain.
- Keep Caddy in local mode: Cloudflare forwards HTTP to `localhost:8080`.
  Caddy's site binds `:8080` for **any** Host, so the tunnel's
  `Host: bot.fertrac.com` reaches the upstream (a site pinned to
  `localhost` would answer empty `200` responses instead of proxying).
- The app still verifies `X-Hub-Signature-256` on `/webhook` exactly as it
  does over Caddy.

### Running cloudflared as a Docker container (recommended, no admin)

Instead of `cloudflared service install` (which needs an elevated prompt on
Windows), run the connector as a compose service that restarts with Docker:

1. Do the one-time setup on the host (steps 5-6 above; this writes
   `%USERPROFILE%\.cloudflared\<tunnel-id>.json`).
2. Edit `deploy/cloudflared/config.yml`: put the tunnel name and your
   hostname (`credentials-file` stays `/etc/cloudflared/credentials.json`).
3. In `.env` set `CLOUDFLARED_CREDENTIALS_FILE` to the host path of your
   `<tunnel-id>.json` (Windows: `C:/Users/<you>/.cloudflared/<id>.json`,
   Linux: `/home/<you>/.cloudflared/<id>.json`). Nothing tunnel-specific is
   hard-coded in the compose files any more.
4. Start everything:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.caddy.yml -f docker-compose.tunnel.yml up -d
   ```
5. Verify:
   ```bash
   curl https://bot.fertrac.com/health
   powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1 -BaseUrl https://bot.fertrac.com -SkipComposeUp
   ```

The container reaches Caddy as `http://caddy:8080` over the compose network,
so it works the same on Docker Desktop (Windows/macOS) and Linux. Caddy only
publishes `/webhook` and `/health`; `/metrics`, `/ready` and the API docs answer
404 from outside.

## Production with direct Caddy HTTPS (only if a real public IP is available)

> Skip this if you are behind NAT/CGNAT — the Cloudflare Tunnel section above
> is the working path. This section only applies once this machine has a public
> IP with ports 80/443 reachable from the internet (no CGNAT).

1. In `.env` set the domain:

   ```env
   CADDY_DOMAIN=bot.example.com
   ```

2. Point a DNS `A` record (and `AAAA` if IPv6) at this machine's public IP.
   Caddy cannot obtain a certificate until this record exists and resolves:
   Let's Encrypt fails with `no valid A records found for <domain>` if it does
   not. Verify from this machine:

   ```bash
   Resolve-DnsName bot.example.com    # must return your public IP
   nslookup bot.example.com
   ```

   (Propagation can take minutes to hours depending on your DNS provider.)
3. Uncomment the `80:80` and `443:443` port mappings in
   `docker-compose.caddy.yml` (already uncommented) — Let's Encrypt must reach
   this machine on port 80 for the http-01 challenge.
4. Open firewall ports 80 and 443 (keep 8000 and 8080 closed to the internet).
5. Start the overlay:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d
   ```

   Caddy obtains the certificate, serves HTTPS, and redirects port 80 → 443.
   After fixing DNS, force a fresh attempt without waiting for the automatic
   backoff:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d --no-deps --force-recreate caddy
   ```

   > If you get an ACME rate-limit error right after adding DNS, wait ~1 hour —
   > Caddy retries automatically (backoff up to 30 days).

6. In `.env`, add the app secret so Meta signatures are verified:

   ```env
   WHATSAPP_APP_SECRET=<App Secret from the Meta app dashboard>
   ```

7. Restart the bot so it picks up the new secret
   (`docker compose up -d --force-recreate chatbot` — a plain `restart` does
   **not** re-read `.env`).
8. In the Meta app dashboard, configure the webhook:

   - **Callback URL:** `https://bot.example.com/webhook`
   - **Verify token:** the same value as `WHATSAPP_VERIFY_TOKEN` in `.env`
   - Subscribe to the **messages** field.

### Running Caddy as a bare process (alternative to Docker)

Download Caddy from caddyserver.com, then point the upstream at the host port:

```bash
CADDY_UPSTREAM=127.0.0.1:8000 caddy run --config deploy/Caddyfile
```

## Verifying the proxy after DNS + certificate

```bash
# health through the real domain (certificate must be valid)
curl https://bot.example.com/health

# full webhook suite against the real URL (signature checks included)
powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1 -BaseUrl https://bot.example.com -SkipComposeUp
```

## Tuning

| Variable | Default | Meaning |
|---|---|---|
| `CADDY_DOMAIN` | `http://localhost:8080` | Site address. Leave unset for local; set a real domain for HTTPS. |
| `CADDY_UPSTREAM` | `whatsapp-qwen-chatbot:8000` | Where to forward requests. |

## Security notes

- With `WHATSAPP_APP_SECRET` set, `/webhook` computes
  `HMAC-SHA256(raw_body, app_secret)` and compares it (constant-time) with the
  `X-Hub-Signature-256` header. Forged requests are rejected with 403 *before*
  any processing.
- If the secret is **not** set, the bot logs a warning and accepts unsigned
  payloads — fine for the test number, but set it before going live.
- The Caddyfile sets `request_body max_size 1MB` and does not re-encode the
  body, so the bot hashes the exact bytes Meta sent.
- Keep ports 8000/8080 closed to the internet; only 80/443 should be open in
  production.
- TLS is end-to-end: Meta ↔ Caddy is HTTPS; Caddy ↔ FastAPI is on the Docker
  network.

## Firewall

On Linux (ufw):

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

On Windows, allow inbound TCP 80/443 through Windows Defender Firewall, and on
your router forward those ports to this machine.

## Useful commands

```bash
# start ONLY the proxy. IMPORTANT: --no-deps skips starting the chatbot, so the
# chatbot container must already be running, or Caddy logs
# "lookup whatsapp-qwen-chatbot ... no such host" and returns 502.
docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d --no-deps caddy

# usual way: start (or restart) everything, caddy included
docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d
```

# logs
docker compose -f docker-compose.yml -f docker-compose.caddy.yml logs -f caddy

# capture the caddy log to logs/wa_chatbot_caddy_log_<timestamp>.log
powershell -ExecutionPolicy Bypass -File scripts\tail-logs.ps1 -Services caddy

# reload config without downtime (after editing Caddyfile)
docker compose -f docker-compose.yml -f docker-compose.caddy.yml exec caddy caddy reload --config /etc/caddy/Caddyfile

# stop the proxy only
docker compose -f docker-compose.yml -f docker-compose.caddy.yml stop caddy

# certificate store lives in the caddy-data volume (persists across restarts)
docker volume ls | grep caddy
```
