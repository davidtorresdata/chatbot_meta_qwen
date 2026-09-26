# WhatsApp Chatbot — Qwen + LanceDB

A modular Python WhatsApp chatbot (Meta Cloud API) that answers strictly from a
local, scalable knowledge base using Retrieval-Augmented Generation (RAG):
Qwen generates replies, LanceDB stores the knowledge, and guardrails prevent
hallucinations.

> **Operación en producción (colas, concurrencia, escalado, seguridad, métricas):**
> ver [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

## Features

- **RAG, not raw LLM** — every answer must be grounded in the knowledge base;
  the bot never invents facts.
- **Local, scalable knowledge base** — LanceDB embedded mode keeps documents on
  disk (`./data/lancedb`). Add more files and re-run ingestion to grow it.
- **Editable conversation tree** — script flows in plain Markdown (`tree.md`):
  forms, option menus, closed answers, and redirects to a human agent. See
  `docs/CONVERSATION_TREE.md`.
- **Smart redirects** — route users to a website or to another WhatsApp number
  (vendor / human agent) via tappable buttons.
- **Persistent action logs** — every inbound message and reply is recorded in a
  timestamped `logs/wa_ollama_logs_<datetime>.txt` that never gets deleted.
- **Conversation registry** — records everyone who contacts the bot
  (hora, numero, nombre, ciudad, empresa, pregunta, respuesta) into a local
  SQLite database **or** a Google Spreadsheet, fully configurable via `.env`.
  See `docs/CONVERSATION_REGISTRY.md`.
- **Easy tuning** — temperature, thresholds, messages, and redirects live in
  `config/config.yaml` (no code changes needed).
- **Modular design** — separate packages for WhatsApp, knowledge, LLM, tree,
  and agent logic, each easily replaceable.
- **Docker ready** — `docker compose up` runs the whole stack (chatbot + LLM +
  embeddings), CPU or GPU.

## Architecture

```
WhatsApp (Meta) ──> HTTPS reverse proxy ──> /webhook (FastAPI) ──> WhatsAppOrchestrator
                                                                     │ 1. forbidden-subject guardrail
                                                                     │ 2. redirect intent check
                                                                     │ 3. conversation tree (forms / menus / redirects)
                                                                     │ 4. embed question
                                                                     │ 5. retrieve top-k from LanceDB
                                                                     │ 6. similarity threshold gate
                                                                     │ 7. Qwen answers from context only
                                                                     │ 8. grounding check
                                                                     ▼
                                                                ReplyAction ──> Meta API (text / CTA button)
```

| Package            | Responsibility                                            |
|--------------------|-----------------------------------------------------------|
| `src/whatsapp`     | Meta Cloud API client (webhook verify, send, redirects, signature check) |
| `src/knowledge`    | Chunker, embedders, LanceDB store, ingestion              |
| `src/llm`          | Qwen client over any OpenAI-compatible endpoint           |
| `src/tree`         | Markdown conversation-tree parser + stateful engine       |
| `src/agent`        | Orchestrator + guardrails + prompts                       |
| `src/registry`     | Conversation registry: contacts + backends (SQLite / Google Sheets) |
| `src/config.py`    | Settings (YAML + env overrides)                           |
| `src/utils`        | Persistent action logging                                 |

## How the anti-hallucination restrictions are enforced

1. **No hallucination**: answers must come from retrieved chunks. If no chunk
   clears `knowledge.score_threshold`, the bot sends the fallback message. After
   generation, the grounding check verifies the answer overlaps the retrieved
   knowledge (`knowledge.enable_grounding_check`); if not, the fallback is sent.
2. **Never talks about how it was built**: a forbidden-subject guardrail
   (`src/agent/guardrails.py`) detects questions about prompts/code/model and
   returns the configured `refusal_message`. The system prompt also forbids it.
3. **Never answers outside the knowledge**: the system prompt instructs Qwen to
   answer *only* from the provided knowledge and to output the fallback message
   otherwise; the retrieval threshold acts as a hard gate.

---

# Deployment — step by step

This guide takes you from an empty server to a working WhatsApp bot. Plan
~30–45 minutes (most of it waiting for model downloads).

## Quick start (the whole journey at a glance)

```bash
copy .env.example .env                            # 1. create configuration
# 2. edit .env: WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN
docker compose up -d                              # 3. start chatbot + ollama (downloads ~5 GB of models)
curl http://localhost:8000/health                 # 4. {"status":"ok","chunks":N}
docker compose exec chatbot python scripts/ingest_cli.py knowledge_base/   # 5. load your knowledge
docker compose exec chatbot python scripts/chat_cli.py                      # 6. talk to the bot locally
docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d      # 7. start the webhook proxy
powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1            # 8. all webhook tests pass
# 9. start ALL services in ONE command (chatbot + ollama + Caddy + Cloudflare Tunnel):
docker compose -f docker-compose.yml -f docker-compose.caddy.yml -f docker-compose.tunnel.yml up -d
# 10. production: APP_ENV=production + all WHATSAPP_* secrets in .env (fail-closed),
#    set CADDY_DOMAIN=bot.example.com, point DNS, open 80/443
# 11. Meta: callback URL https://bot.fertrac.com/webhook + your verify token; subscribe to `messages`
```

Every step below explains one of these lines in detail.

## Step 0 — Prerequisites

- A server (or machine) with **Docker + Docker Compose**, **~8 GB free RAM** and
  **~6 GB free disk** (models + knowledge). CPU-only is fine. Verify the tools
  are installed:

  ```bash
  docker --version            # Docker
  docker compose version      # Compose v2
  ```

- A **public HTTPS URL** that reaches your machine (a domain, or a tunnel such
  as ngrok/cloudflared for testing). Meta requires HTTPS for webhooks.
- Optional but recommended: a **Meta developer account** for the WhatsApp
  Cloud API test number (free; no business registration needed to test).

## Step 1 — Prepare the configuration

```bash
copy .env.example .env        # Windows
cp  .env.example .env         # Linux/macOS
```

Edit `.env` and fill in at least the WhatsApp values:

```env
# ---- WhatsApp (Meta Cloud API) ----
WHATSAPP_ACCESS_TOKEN=your_meta_access_token
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id
WHATSAPP_VERIFY_TOKEN=any_secret_string_you_choose
WHATSAPP_APP_SECRET=your_meta_app_secret        # optional but recommended (webhook signature check)

# ---- Qwen LLM (served by the bundled Ollama container) ----
LLM_BASE_URL=http://localhost:8001/v1
LLM_API_KEY=EMPTY
LLM_MODEL=qwen3.5:4b

# ---- Embeddings for RAG ----
EMBEDDING_BACKEND=openai_compat
EMBEDDING_API_KEY=EMPTY
EMBEDDING_MODEL=bge-m3
```

Leave `LLM_*` and `EMBEDDING_*` at these defaults — they point at the bundled
Ollama container. **Never commit `.env`** (it is git-ignored). If you leave the
WhatsApp credentials empty, the bot still runs and is fully testable locally
(Step 5).

## Step 2 — Start the stack

Base stack (chatbot + ollama):

```bash
docker compose up -d
```

**All 4 services in one command** (chatbot + ollama + Caddy proxy + Cloudflare
Tunnel). Requires the tunnel configured first — see `deploy/README.md` →
*Real domain via Cloudflare Tunnel* (DNS in Cloudflare + `docker compose -f
docker-compose.yml -f docker-compose.caddy.yml -f docker-compose.tunnel.yml`):

```bash
docker compose -f docker-compose.yml -f docker-compose.caddy.yml -f docker-compose.tunnel.yml up -d
```

Either way, the first run downloads the AI models (~5 GB, take a coffee break):

| Service      | Host port | Purpose                                             |
|--------------|-----------|-----------------------------------------------------|
| `chatbot`    | 8000      | WhatsApp webhook + RAG (FastAPI)                    |
| `ollama`     | 8001      | Qwen 3.5 + embeddings, OpenAI-compatible `/v1`      |
| `caddy`      | 8080      | Reverse proxy → chatbot (listens on `:8080`, any Host) |
| `cloudflared`| —         | Cloudflare Tunnel: HTTPS `bot.fertrac.com` → Caddy  |

Watch the model pulls finish:

```bash
docker compose logs -f ollama     # Ctrl+C once you see "success" for each model
```

## Step 3 — Verify everything is healthy

```bash
docker compose ps                  # both services should say "healthy"
curl http://localhost:8001/v1/models   # lists qwen3.5:4b and bge-m3
curl http://localhost:8000/health      # {"status":"ok","chunks":N}
```

A quick end-to-end LLM check against Ollama:

```bash
curl http://localhost:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.5:4b","messages":[{"role":"user","content":"Say hello"}],"max_tokens":128}'
```

## Step 4 — Ingest your knowledge base

Put your documents in `knowledge_base/` (`.txt`, `.md`, `.pdf`, `.xlsx` — one
Excel data row = one searchable block). Full rules, layout tips and
troubleshooting: `docs/ADD_KNOWLEDGE.md`.

```bash
docker compose exec chatbot python scripts/ingest_cli.py knowledge_base/
docker compose exec chatbot python scripts/ingest_cli.py docs/            # dirs work too
docker compose exec chatbot python scripts/ingest_cli.py faq.pdf --reset  # drop & rebuild table
docker compose exec chatbot python scripts/ingest_cli.py pricing.xlsx     # single Excel file
```

Verify the count: `curl http://localhost:8000/health` — the `chunks` number
should be > 0.

## Step 5 — Test locally (no WhatsApp needed)

Simulate a phone conversation through the real pipeline (tree + RAG + Qwen)
without any Meta app:

```bash
docker compose exec chatbot python scripts/chat_cli.py       # interactive REPL
echo "menu" | docker compose exec chatbot python scripts/chat_cli.py   # scripted
```

Commands inside the REPL: `/reset` starts a new conversation, `/quit` exits.
You can also script the tree and redirects now:

- **Conversation tree** — edit `tree.md` (forms, option menus, closed answers,
  human-agent redirects). Restart to reload: `docker compose restart chatbot`.
  Reference: `docs/CONVERSATION_TREE.md`.
- **Redirects** — keyword → website/vendor rules in `config/config.yaml`
  (`redirects:` section).

Every action is already being logged to `logs/wa_ollama_logs_<datetime>.txt`
(rotated, purged after `LOG_RETENTION_DAYS`, phones masked) — check it with
`Get-ChildItem logs`.

## Step 6 — Add the reverse proxy (Caddy, works out of the box)

You do **not** need to expose the whole app, only `/webhook`. The repo ships a
Caddy reverse proxy that works **out of the box with no configuration** — by
default it listens on `http://localhost:8080` and forwards to the chatbot
container. Start it:

```bash
docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d
curl http://localhost:8080/health        # {"status":"ok","chunks":N} -> proxy works
```

**For production HTTPS** (Meta requires a public HTTPS URL): on a host with a
public IP, Caddy obtains a free Let's Encrypt certificate automatically — but
**this machine is behind NAT/CGNAT, so use the Cloudflare Tunnel path below
instead of the direct-cert steps 1–5.**

The direct-cert path (only once a real public IP is available):

1. In `.env` set the domain: `CADDY_DOMAIN=bot.example.com`.
2. Point a DNS `A` record at this machine's public IP. **Caddy cannot get a
   certificate until this resolves** — Let's Encrypt fails with
   `no valid A records found for <domain>` if it doesn't. Verify:
   `Resolve-DnsName bot.example.com` (must return your public IP).
3. The `80:80` / `443:443` mappings in `docker-compose.caddy.yml` are already
   uncommented — Let's Encrypt needs port 80 reachable for the http-01
   challenge. (If you are on a host where 80/443 are already taken, comment
   them back out and revert to the local 8080 mode.)
4. Open firewall ports 80/443 (leave 8000/8080 closed to the internet).
5. Start the stack: `docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d`.

**Test the webhook through the proxy** — run the bundled suite (health +
verification handshake + signature checks), no Meta needed:

```bash
# local mode (default): http://localhost:8080
powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1

# production / domain mode: test the real HTTPS URL
powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1 -BaseUrl https://bot.example.com -SkipComposeUp
```

**If you are behind NAT/CGNAT** (e.g. two different public IPs reported by
`ifconfig.me` vs `api.ipify.org`) — as this machine is — Caddy cannot obtain a
certificate directly and no router setting will help. Use a **Cloudflare
Tunnel** with the real domain instead: Cloudflare terminates HTTPS for
`bot.fertrac.com` and connects back to Caddy in local mode. See
`deploy/README.md` → *Real domain via Cloudflare Tunnel* for the full
step-by-step (needs a free Cloudflare account + changing `fertrac.com`
nameservers to Cloudflare in GoDaddy).

A quick ad-hoc tunnel (`scripts\tunnel.ps1`, random `trycloudflare.com` URL)
is fine for fast tests, but its URL changes every run, so it is not suitable
for the Meta callback long-term.

Full guide (Caddy, firewall, certs, tuning): `deploy/README.md`.

## Step 7 — Connect WhatsApp (Meta)

1. Go to [Meta for Developers](https://developers.facebook.com) and create an
   app of type **Business**.
2. Add the **WhatsApp** product to the app.
3. In **API Setup**, link a phone number (use the **test number** during
   development; it can message up to 5 test recipients with no business
   verification).
4. Copy the **Access token**, **Phone number ID**, and the **App Secret**
   (App settings → Basic) into `.env`. Your `WHATSAPP_VERIFY_TOKEN` can be any
   string you choose — it just has to match what you put in Meta.
5. Restart the bot so it picks up the new settings:

   ```bash
   docker compose up -d --force-recreate chatbot    # restart is NOT enough for .env changes
   ```

6. In Meta, WhatsApp → **Configuration**:
   - **Callback URL:** `https://bot.fertrac.com/webhook` (the hostname routed
     to the Cloudflare Tunnel — note the `/webhook` path)
   - **Verify token:** the same value as `WHATSAPP_VERIFY_TOKEN`
   - Click **Verify and save** — it should turn "Configured".
7. In **Webhooks**, subscribe to the `messages` field for your WhatsApp
   Business Account.

> With `WHATSAPP_APP_SECRET` set, the bot rejects any webhook whose
> `X-Hub-Signature-256` header does not match, so forged requests are dropped
> before any processing.

## Step 8 — Go live

Message your WhatsApp number. The bot should reply. Then:

- Watch the action log live: `docker compose logs -f chatbot` and/or read
  `logs/wa_ollama_logs_<datetime>.txt`.
- Test a conversation-tree flow (e.g. type `menu`).
- Test a redirect (e.g. a message containing a keyword from `redirects.rules`).

## Step 9 — Day-to-day operations

| Task | Command |
|---|---|
| Start the bot | `docker compose up -d` |
| Start **everything** (all 4 services) | `docker compose -f docker-compose.yml -f docker-compose.caddy.yml -f docker-compose.tunnel.yml up -d` |
| Stop the bot | `docker compose down` (keeps knowledge & models) |
| See live chat logs | `docker compose logs -f chatbot` |
| See AI model logs | `docker compose logs -f ollama` |
| Health check | `curl http://localhost:8000/health` |
| Restart after `config.yaml` changes | `docker compose restart chatbot` |
| Reload after `.env` changes | `docker compose up -d --force-recreate chatbot` |
| Reload after `tree.md` changes | `docker compose restart chatbot` |
| Read action log files | `Get-ChildItem logs` (host) |
| Start the webhook proxy | `docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d` |
| Start **only** the webhook proxy | `docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d --no-deps caddy` (chatbot must already be running — otherwise Caddy returns 502 with `lookup whatsapp-qwen-chatbot ... no such host`) |
| Stop the webhook proxy | `docker compose -f docker-compose.yml -f docker-compose.caddy.yml stop caddy` |
| Test the webhook through the proxy | `powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1` |
| See proxy logs | `docker compose -f docker-compose.yml -f docker-compose.caddy.yml logs -f caddy` |
| Reload proxy config | `docker compose -f docker-compose.yml -f docker-compose.caddy.yml exec caddy caddy reload --config /etc/caddy/Caddyfile` |
| Capture per-service logs (one-shot) | `powershell -ExecutionPolicy Bypass -File scripts\tail-logs.ps1` |
| Stream per-service logs live | `powershell -ExecutionPolicy Bypass -File scripts\tail-logs.ps1 -Follow` |

**Per-service logs** — `scripts/tail-logs.ps1` classifies each service's container
log into its own dated file, so you always have one file per service:

```text
logs/wa_chatbot_chatbot_20260804_103000.log      # FastAPI app (uvicorn)
logs/wa_chatbot_ollama_20260804_103000.log       # Ollama API + model pulls
logs/wa_chatbot_caddy_20260804_103000.log        # Caddy proxy + certificate events
logs/wa_chatbot_cloudflared_20260804_103000.log  # Cloudflare Tunnel connector
```

Use `-Services caddy` to capture only one service, and `-Follow` to stream
live. These cover container stdout/stderr; the chatbot's own per-turn action
log stays in `logs/wa_ollama_logs_*.txt`. Docker's internal JSON logs per
container are capped at 10 MB × 3 files (see the `logging:` blocks in the
compose files).

Backup (see `docs/USER_MANUAL.md` §15): copy `knowledge_base/`, `data/`,
`tree.md`, `logs/`, and `.env` to safe storage.

## Step 10 — Production readiness checklist

Before pointing real customers at the bot, go through this:

- [ ] **Real WhatsApp number**: a live WhatsApp Business number (Meta business
      verification) and a **permanent** access token — the test number is only
      for development.
- [ ] **`WHATSAPP_APP_SECRET` set** in `.env` (webhook signature validation
      active). Recreate the bot afterwards:
      `docker compose up -d --force-recreate chatbot`.
- [ ] **`APP_ENV=production`** in `.env`: the bot refuses to start if a
      WhatsApp secret is missing or config/tree still contain placeholders.
- [ ] **HTTPS**: `CADDY_DOMAIN=bot.example.com` in `.env`, DNS `A` record,
      ports 80/443 open in the firewall (or the Cloudflare Tunnel overlay).
- [ ] **Ports 8000, 8001 and 8080 are bound to 127.0.0.1** (default) — only
      80/443 (or the tunnel) are public; Caddy only serves `/webhook` and `/health`.
- [ ] **`/ready` returns 200** (`curl http://localhost:8000/ready`).
- [ ] **Queue sized**: `QUEUE_WORKERS` = `OLLAMA_NUM_PARALLEL`; for zero message
      loss on restarts or more than one replica, use `docker-compose.redis.yml`.
- [ ] **Monitoring**: `docker-compose.monitoring.yml` up, alerts reviewed.
- [ ] **Webhook tests pass**: `powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1`.
- [ ] **Knowledge ingested**: `curl http://localhost:8000/health` shows
      `chunks` > 0.
- [ ] **Backups planned**: copy `knowledge_base/`, `data/`, `tree.md`,
      `logs/`, and `.env` to safe storage on a schedule.
- [ ] **Support path works**: try a conversation-tree flow, a redirect keyword,
      and a question the knowledge base cannot answer (it must get the
      fallback message, never an invented answer).

## Step 11 — Troubleshooting

| Symptom | Fix |
|---|---|
| Services never become healthy | First start downloads ~5 GB of models. Watch `docker compose logs -f ollama` and wait for the pulls to finish. |
| `chunks: 0` in `/health` | You haven't ingested yet: `docker compose exec chatbot python scripts/ingest_cli.py knowledge_base/`. |
| Meta shows "Callback verification failed" | Verify token mismatch. The URL must end in `/webhook` and be reachable over public HTTPS; check `docker compose logs chatbot` to see the `GET /webhook` from Meta. |
| `403 Invalid signature` on webhook | `WHATSAPP_APP_SECRET` must equal the Meta App Secret, and the hash is computed over the exact raw body — don't re-encode the request (the Caddyfile already preserves it). |
| `port is already allocated` (8080/80/443) | Another service uses that port. Change/stop it, or remove the conflicting port mapping from `docker-compose.caddy.yml`. |
| Bot always replies with the fallback message | Retrieval threshold too strict (`knowledge.score_threshold` too high) or knowledge missing/out of date. Re-ingest with `--reset` and lower the threshold. |
| Empty or truncated replies | `qwen3.5:4b` is a thinking model and can burn tokens on reasoning; raise `llm.max_tokens` in `config.yaml`. |
| Webhook accepts payloads (no 403) even with a bad signature | `WHATSAPP_APP_SECRET` is not set, so validation is disabled. Set it — see Step 1 and `docs/USER_MANUAL.md` §5. |

More in-depth guidance: `docs/USER_MANUAL.md` §13 (troubleshooting) and
`docs/ADD_KNOWLEDGE.md` §9.

## Windows notes

- Use **`curl.exe`** in PowerShell — plain `curl` is an alias for
  `Invoke-WebRequest` and behaves differently.
- Avoid inline `python -c "..."` in PowerShell: quoting gets mangled. Use the
  provided scripts (`scripts/chat_cli.py`, `scripts/ingest_cli.py`) or a `.py`
  file instead.
- After editing `.env`, use **`docker compose up -d --force-recreate chatbot`**
  — `docker compose restart chatbot` does **not** re-read `.env`.
- The webhook test suite is a PowerShell script: run it as
  `powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1`.

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The suite uses fake embedders and a fake LLM, so it runs without Docker, an
LLM, or WhatsApp credentials. Redis tests use `REDIS_URL` or a local
`redis-server` binary and are skipped otherwise. CI: `.github/workflows/ci.yml`.

Load test (staging, never production numbers):

```bash
python scripts/mock_services.py --port 9100 --llm-latency 1.5 --llm-parallel 2
# bot with WHATSAPP_GRAPH_BASE_URL/LLM_BASE_URL/EMBEDDING_BASE_URL -> http://localhost:9100(/v1)
python scripts/load_test.py --url http://localhost:8000 --secret $WHATSAPP_APP_SECRET \
    --phones 50 --messages 4 --mock http://localhost:9100 --metrics
```

## Tuning (no code changes)

| What                            | Where                                            |
|---------------------------------|--------------------------------------------------|
| Conversation temperature        | `config.yaml` → `llm.temperature` (or `TEMPERATURE` env) |
| Max answer length / top_p       | `config.yaml` → `llm.max_tokens` / `top_p`       |
| How strict retrieval must be    | `config.yaml` → `knowledge.score_threshold`      |
| Redirect to website / vendor    | `config.yaml` → `redirects.rules` (keyword → url or whatsapp) |
| Conversation-tree flows         | `tree.md` (see `docs/CONVERSATION_TREE.md`); enable/disable via `config.yaml` → `tree.enabled` |
| Fallback & refusal messages     | `config.yaml` → `agent.*_message`                |
| Knowledge chunk granularity     | `config.yaml` → `storage.chunk_size` / `chunk_overlap` |
| Embedding backend               | `.env` → `EMBEDDING_BACKEND` (`openai_compat` \| `local`) |

### Switching models

- **Chat LLM**: edit `LLM_MODEL` in `.env` (any `ollama pull`-able tag, e.g.
  `qwen3.5:9b`, `qwen3:35b-a3b`), and add the tag to the `ollama pull` list in
  `docker-compose.yml` so it is fetched on start.
- **Embeddings**: edit `EMBEDDING_MODEL`, then **re-ingest** the knowledge base
  (`docker compose exec chatbot python scripts/ingest_cli.py knowledge_base/ --reset`).
  Changing the embedding model invalidates the existing LanceDB table.

### GPU-only alternative — vLLM (`qwen-service/`)

Serves Qwen 3.5 with **vLLM** (CUDA) on NVIDIA hosts — larger models, faster
generation. This stack is *not* the default: it needs an NVIDIA GPU +
`nvidia-container-toolkit`. See `qwen-service/runDockerFile.txt` and
`qwen-service/README.md` for GPU/CPU vLLM builds, model switching, port changes
and HTTPS setup.

### Using any other OpenAI-compatible endpoint

Point `LLM_BASE_URL` at any endpoint that speaks the OpenAI API:

```bash
# standalone Ollama
ollama serve        # LLM_BASE_URL=http://localhost:11434/v1, LLM_MODEL=qwen3.5:4b

# vLLM (GPU only)
vllm serve Qwen/Qwen3.5-35B-A3B --served-model-name Qwen/Qwen3.5-35B-A3B
```

### Local development (no Docker)

```bash
python -m venv .venv
.venv\Scripts\activate                       # Windows
pip install -r requirements.txt              # + requirements-local.txt for offline embeddings
copy .env.example .env                       # Meta credentials + LLM_BASE_URL
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000
```

For a local LLM, install Ollama and `ollama pull qwen3.5:4b bge-m3`, then set
`LLM_BASE_URL=http://localhost:11434/v1` in `.env`.

## Notes / scaling

- Messages go through a bounded queue with per-conversation ordering;
  conversation state has an idle TTL. `memory` backends = one instance;
  `docker-compose.redis.yml` = durable queue + shared state + N replicas.
  Details in `docs/OPERATIONS.md`.
- The grounding check is a lightweight lexical heuristic; the retrieval
  similarity gate is the primary anti-hallucination control — tune
  `knowledge.score_threshold` per your embedding model.
- `qwen3.5:4b` is a *thinking* model: it spends tokens on reasoning before the
  answer. The default `llm.max_tokens` is 512; if you ever see empty replies on
  long questions, raise `max_tokens` or pick a non-thinking variant.
- Tree keywords are substring matches: a flow keyword like `return` will match
  any message containing that word, shadowing the RAG answer. Keep flow
  keywords specific (see `docs/CONVERSATION_TREE.md`).

## Project layout

```
├── config/config.yaml      # tunable settings (+ optional config.<APP_ENV>.yaml overlay)
├── knowledge_base/         # source documents (.txt/.md/.pdf/.xlsx)
├── data/lancedb/           # LanceDB knowledge base (created on ingest)
├── logs/                   # rotated action logs (wa_ollama_logs_*.txt)
├── tree.md                 # conversation-tree flows (safe to edit in place)
├── scripts/
│   ├── ingest_cli.py       # knowledge ingestion CLI
│   ├── chat_cli.py         # local WhatsApp simulator (no Meta needed)
│   ├── registry_cli.py     # view/export the conversation registry (SQLite)
│   ├── load_test.py        # signed webhook load generator
│   ├── mock_services.py    # mock Meta Graph API + LLM for load tests
│   └── tail-logs.ps1       # per-service log collector (wa_chatbot_<svc>_log_*.log)
├── src/
│   ├── main.py             # FastAPI webhook server (/webhook /health /ready /metrics)
│   ├── pipeline.py         # worker-side message processing
│   ├── config.py           # settings loader + validation (fail-closed in production)
│   ├── dispatch/           # message queue (memory | redis), per-phone ordering
│   ├── state/              # conversation state, dedup, rate limit (memory | redis)
│   ├── agent/              # orchestrator, guardrails, prompts
│   ├── knowledge/          # chunker, embedding, vector store, ingest
│   ├── llm/                # Qwen OpenAI-compatible client
│   ├── registry/           # conversation registry (contacts + backends)
│   ├── tree/               # conversation-tree parser + engine
│   ├── utils/              # logging, PII masking, resilience, metrics, redis client
│   └── whatsapp/           # Meta Cloud API client
├── deploy/
│   ├── Caddyfile           # reverse proxy config (only /webhook and /health public)
│   ├── monitoring/         # Prometheus config, alerts, Grafana provisioning
│   ├── README.md           # reverse-proxy + firewall guide
│   └── test-webhook.ps1    # webhook test suite (health + signature checks)
├── docs/                   # user manual, technical doc, OPERATIONS, how-to guides
├── tests/                  # pytest suite
├── qwen-service/           # vLLM chat service (GPU-only, NVIDIA)
├── .github/workflows/ci.yml  # CI: tests (+redis), secrets scan, image build
├── Dockerfile              # non-root image
├── docker-compose.yml      # chatbot + ollama (LLM & embeddings), host-only ports
├── docker-compose.redis.yml      # durable queue + shared state (overlay)
├── docker-compose.caddy.yml      # optional Caddy proxy overlay (local first)
├── docker-compose.tunnel.yml     # Cloudflare Tunnel overlay
├── docker-compose.monitoring.yml # Prometheus + Grafana overlay
├── pyproject.toml          # metadata + dependency groups
├── requirements*.txt       # runtime / dev / local-embeddings deps
└── ollama-models/          # Ollama model cache + private key (git-ignored)
```
