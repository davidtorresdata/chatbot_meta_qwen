# Technical Documentation — WhatsApp Chatbot (Qwen + LanceDB)

Developer-oriented reference for the WhatsApp RAG chatbot. For operators and
end users, see the [User Manual](USER_MANUAL.md). Production operation
(queue sizing, scaling, security, metrics, runbook): [OPERATIONS.md](OPERATIONS.md).

- [1. Overview](#1-overview)
- [2. Architecture](#2-architecture)
- [3. Technology stack](#3-technology-stack)
- [4. Repository layout](#4-repository-layout)
- [5. Message processing pipeline](#5-message-processing-pipeline)
- [6. Configuration system](#6-configuration-system)
- [7. Module reference](#7-module-reference)
- [8. HTTP API](#8-http-api)
- [9. Data model & storage](#9-data-model--storage)
- [10. Embedding backends](#10-embedding-backends)
- [11. Deployment](#11-deployment)
- [12. Testing](#12-testing)
- [13. Extending the project](#13-extending-the-project)
- [14. Security considerations](#14-security-considerations)
- [15. Queue, state and resilience](#15-queue-state-and-resilience)
- [16. Observability](#16-observability)

---

## 1. Overview

The project is a FastAPI webhook server that receives WhatsApp messages from
the **Meta Cloud API**, runs a **Retrieval-Augmented Generation (RAG)**
pipeline, and replies via the Meta API. Answers must be grounded in a local,
vector-searchable knowledge base (LanceDB). Three guardrails enforce the
product's core rules: no hallucination, no discussion of how the bot was built,
and no answering outside the knowledge base.

The application is LLM-agnostic: it talks to **any OpenAI-compatible `/v1`
endpoint**. The shipped default is **Ollama** serving `qwen3.5:4b` (chat) and
`bge-m3` (embeddings); a **vLLM** (CUDA) stack in `qwen-service/` is provided
for GPU hosts that need larger models.

## 2. Architecture

```
 WhatsApp (Meta Cloud API)
        │  POST /webhook (JSON)                     GET /webhook (hub.* verification)
        ▼
┌───────────────────────────────────────────────────────────────────┐
│ src/main.py — FastAPI app (request path, < 50 ms)                  │
│  • /webhook  GET   verify_webhook() → echoes hub.challenge         │
│  • /webhook  POST  signature → dedup(msg_id) → rate limit →        │
│                    truncate → dispatcher.submit() → 200            │
│  • /health /ready /metrics                                         │
└──────────────┬────────────────────────────────────────────────────┘
               ▼  InboundMessage
┌───────────────────────────────────────────────────────────────────┐
│ src/dispatch/ — queue (memory | redis)                             │
│  per-phone FIFO mailbox · K workers · backpressure · drain         │
└──────────────┬────────────────────────────────────────────────────┘
               ▼  src/pipeline.py — MessageProcessor (timeout, fallback)
┌───────────────────────────────────────────────────────────────────┐
│ src/agent/orchestrator.py — WhatsAppOrchestrator                   │
│  0. load conversation state (src/state/, TTL) … save at the end    │
│  1. Guardrails.forbidden_subject()   → refusal if "how were you    │
│                                          built / your prompt..."   │
│  2. Guardrails.match_redirect()      → CTA button (url / wa.me)    │
│  3. embed question (embedder)                                      │
│  4. vector_store.search_async()      → top-k hits (LanceDB)        │
│  5. Guardrails.retrieval_ok()        → similarity threshold gate   │
│  6. build_system_prompt() + conversation history                   │
│  7. qwen.chat()  (circuit breaker)   → answer from context only    │
│  8. Guardrails.is_grounded()         → lexical overlap check       │
└──────────────┬────────────────────────────────────────────────────┘
               ▼  ReplyAction{type, message, url, whatsapp}
┌───────────────────────────────────────────────────────────────────┐
│ src/whatsapp/meta.py — MetaWhatsAppClient                          │
│  • send_text()        • send_cta_url() (open_link button)          │
│  • mark_read()        • retries on 429 / 5xx (Retry-After)         │
└────────────────────────────────────────────────────────────────────┘
               │
               ▼  POST https://graph.facebook.com/v21.0/<phone_number_id>/messages
        WhatsApp user receives the reply
               │
               ▼  src/registry/ — single background writer, batched rows
```

Component responsibilities:

| Component | Responsibility |
|---|---|
| `src/main.py` | HTTP surface: webhook verification, validated/deduplicated/rate-limited ingestion into the queue, `/health`, `/ready`, `/metrics`; `AppComponents` dependency injection |
| `src/dispatch/` | Inbound queue: `LocalDispatcher` (in-process) and `RedisDispatcher` (durable, multi-replica) |
| `src/pipeline.py` | `MessageProcessor`: worker-side processing with timeout, fallback, send and registry |
| `src/state/` | Conversation snapshots (TTL), message dedup, per-phone rate limit (memory / Redis) |
| `src/agent/orchestrator.py` | End-to-end RAG pipeline; loads/saves per-phone state around each message |
| `src/agent/guardrails.py` | Forbidden-subject, redirect-intent, retrieval-threshold, grounding checks |
| `src/agent/prompts.py` | System-prompt template |
| `src/knowledge/vector_store.py` | LanceDB CRUD + cosine-similarity search |
| `src/knowledge/embedding.py` | Pluggable embedders (OpenAI-compatible / local sentence-transformers) |
| `src/knowledge/chunker.py` | Pure-Python overlapping text splitter |
| `src/knowledge/ingest.py` | File loading (.txt/.md/.pdf) + ingest pipeline |
| `src/llm/qwen.py` | Async OpenAI-compatible chat client with retries + circuit breaker |
| `src/whatsapp/meta.py` | Meta Cloud API client (send, redirects, read receipts, webhook verify, retries) |
| `src/config.py` | Pydantic settings: `config.yaml` + `config.<APP_ENV>.yaml` + `.env` overrides; `validate_settings` |
| `src/utils/` | Rotating logs, PII masking, circuit breaker/backoff, Prometheus metrics, Redis client |

## 3. Technology stack

| Concern | Choice |
|---|---|
| Web framework | FastAPI + uvicorn (Python 3.11 image) |
| Settings | Pydantic v2 + `python-dotenv` + `PyYAML` |
| Vector store | LanceDB embedded mode (on-disk, no server) |
| LLM client | `openai` SDK (`AsyncOpenAI`) against any `/v1` endpoint |
| WhatsApp client | `httpx.AsyncClient` against Meta Graph API |
| PDF parsing | `pypdf` |
| Chat LLM (default) | Ollama `qwen3.5:4b` (OpenAI-compatible) |
| Embeddings (default) | Ollama `bge-m3` (`/v1/embeddings`) |
| Embeddings (offline) | `sentence-transformers` (`EMBEDDING_BACKEND=local`) |
| Queue / shared state | in-process (`asyncio`) or Redis 8 (`redis.asyncio`, Lua scripts) |
| Metrics | `prometheus-client`; Prometheus + Grafana overlay |
| Tests | `pytest` + `fastapi.testclient` (+ real Redis when available); GitHub Actions CI |
| Containerization | `Dockerfile` (non-root) + compose base and overlays (redis, caddy, tunnel, monitoring) |

## 4. Repository layout

```
├── config/config.yaml         # tunable settings (safe to edit in place)
│   config.<APP_ENV>.yaml      # optional per-environment overlay (e.g. production)
├── knowledge_base/            # RAG source documents (.txt/.md/.pdf/.xlsx)
├── data/lancedb/              # LanceDB knowledge base (created on ingest)
├── logs/                      # rotated action logs (wa_ollama_logs_*.txt, purged by age)
├── tree.md                    # conversation-tree flows (safe to edit in place)
├── ollama-models/             # Ollama model cache + private key (git-ignored)
├── scripts/ingest_cli.py      # CLI to chunk + embed + store documents
├── scripts/load_test.py       # signed webhook load generator
├── scripts/mock_services.py   # mock Meta Graph API + OpenAI-compatible LLM
├── deploy/monitoring/         # Prometheus config, alerts, Grafana provisioning
├── src/
│   ├── main.py                # FastAPI app + routes
│   ├── pipeline.py            # worker-side message processing
│   ├── config.py              # settings loader + validation
│   ├── dispatch/              # queue: local + redis dispatchers
│   ├── state/                 # conversation state, dedup, rate limit
│   ├── registry/              # conversation registry (sqlite / google sheets)
│   ├── agent/                 # orchestrator, guardrails, prompts
│   ├── knowledge/             # chunker, embedding, ingest, vector_store
│   ├── llm/                   # qwen client
│   ├── tree/                  # conversation-tree parser + engine
│   ├── utils/                 # logging, pii, resilience, metrics, redis client
│   └── whatsapp/              # meta client
├── tests/                     # pytest suite
├── graphify-out/              # knowledge graph (code AST + docs), see README
├── qwen-service/              # GPU-only vLLM serving stack (alternative)
├── .github/workflows/ci.yml   # tests (+redis), secrets scan, compose + image build
├── Dockerfile                 # non-root image
├── docker-compose.yml         # chatbot + ollama (host-only ports)
├── docker-compose.redis.yml   # durable queue + shared state (overlay)
├── docker-compose.caddy.yml   # reverse proxy (overlay)
├── docker-compose.tunnel.yml  # Cloudflare Tunnel (overlay)
├── docker-compose.monitoring.yml # Prometheus + Grafana (overlay)
├── pyproject.toml             # project metadata + dependency groups
├── requirements.txt           # runtime deps (image)
├── requirements-dev.txt       # + pytest
└── requirements-local.txt     # optional offline-embedding deps
```

## 5. Message processing pipeline

The webhook only validates and enqueues (section 15), so Meta receives an
immediate `{"status":"received"}`. A dispatcher worker then runs
`MessageProcessor.__call__` (`src/pipeline.py`) under
`queue.processing_timeout_seconds`, which calls
`WhatsAppOrchestrator.handle_message` (`src/agent/orchestrator.py`). The
orchestrator first loads the phone's snapshot (history + tree session) from the
state store and saves it back when done; the dispatcher guarantees that one
phone is never processed concurrently. Steps:

1. **Normalize** — strip whitespace; empty text → fallback action.
2. **Forbidden-subject guardrail** (`guardrails.forbidden_subject`, `src/agent/guardrails.py:52`)
   — 9 compiled regexes detect questions about how the bot was built, its
   prompt/code/model. On match, reply with `agent.refusal_message`. If the
   phone is already inside a tree flow, the tree is consulted first (step 3
   below is skipped for it).
3. **Redirect guardrail** (`guardrails.match_redirect`, `src/agent/guardrails.py:60`)
   — if `redirects.enabled`, scans rules in order; the first rule whose keyword
   is a substring of the (lowercased) message wins. Produces a `redirect`
   action: a CTA-URL button to `rule.url`, or to `https://wa.me/<digits>` when
   `rule.whatsapp` is set.
4. **Conversation tree** (`TreeEngine.handle`, `src/tree/engine.py`) — if
   `tree.enabled`, continues the phone's active session or starts a flow on a
   keyword/`menu` match; returns a `ReplyAction` or `None` to continue below.
5. **Retrieve** (`_retrieve`, `src/agent/orchestrator.py`)
   — embed the question (one vector) and run a vector search for `top_k`
   neighbours.
6. **Threshold gate** (`guardrails.retrieval_ok`, `src/agent/guardrails.py:86`)
   — if there are no hits, or the top hit similarity < `knowledge.score_threshold`,
   return the fallback action. This is the hard anti-hallucination gate.
7. **Context + history** — build a numbered context block
   (`[1] …\n---\n[2] …`), fetch the per-phone `Conversation`, trim to
   `agent.history_size` turns, append the new user turn.
8. **Generate** — `QwenClient.chat(system, history)` calls the OpenAI-compatible
   endpoint with `temperature`, `max_tokens`, `top_p`.
9. **Grounding check** (`guardrails.is_grounded`, `src/agent/guardrails.py:71`)
   — tokenize the answer (stopwords removed), compute the fraction of answer
   tokens present in the concatenated retrieved context; if below
   `knowledge.grounding_min_overlap` → fallback. Optionally disabled via
   `knowledge.enable_grounding_check`.
10. **Reply** — record the turn in history and return a `text` action.

`ReplyAction` (`src/agent/orchestrator.py`) is a dataclass: `type`
(`"text" | "redirect" | "fallback"`), `message`, optional `url`/`whatsapp`.
`MessageProcessor._send` (`src/pipeline.py`) maps it to a Meta API call, after
`mark_read`. Timeouts, `CircuitOpenError` and unexpected errors become the
`agent.fallback_message`; non-text messages get `agent.unsupported_message`.
Every outcome is counted in `metabot_messages_processed_total{action,outcome}`.

## 6. Configuration system

### 6.1 Precedence

`load_settings()` (`src/config.py:139`):

1. Load `.env` from the project root (via `python-dotenv`).
2. Load `config/config.yaml` (or `CONFIG_PATH` env, or explicit arg).
3. Deep-merge the profile overlay `config/config.<APP_ENV>.yaml` if it exists
   (e.g. `config.production.yaml` with the real URLs and numbers).
4. Validate with Pydantic `Settings.model_validate`.
5. Overlay environment variables (`_apply_env_overrides`).

Env vars win over the YAML. Both are optional — every field has a default.

At startup `create_app` calls `validate_settings()`: missing WhatsApp secrets,
invalid backends, a redis backend without `REDIS_URL`, or placeholders
(`example.com`, `15551234567`, `acme`) left in the config or `tree.md`. With
`APP_ENV=production` any problem raises `ConfigError` (**fail-closed**, the app
does not start); in other environments each problem is logged as a warning.

### 6.2 `config/config.yaml` reference

| Section | Key | Default | Meaning |
|---|---|---|---|
| `app` | `name` | `AcmeAssistant` | App title / log banner |
| `app` | `company` | `ACME Corp` | Company name used in prompts |
| `app` | `language` | `en` | Conversation language for the prompt |
| `agent` | `history_size` | `6` | Prior turns kept per conversation |
| `agent` | `welcome_message` | `""` | (reserved; prompt-level) |
| `agent` | `fallback_message` | `""` | Sent when knowledge is insufficient |
| `agent` | `refusal_message` | `""` | Sent on forbidden-subject match |
| `agent` | `busy_message` | (es) | Sent when the queue is full |
| `agent` | `queued_message` | (es) | "We got your message" notice when the backlog is large (max once per phone every 5 min) |
| `agent` | `rate_limited_message` | (es) | Sent once per window when a phone exceeds the rate limit |
| `agent` | `unsupported_message` | (es) | Reply to image/audio/video/document/sticker/location/contacts |
| `knowledge` | `top_k` | `5` | Chunks retrieved per question (1–50) |
| `knowledge` | `score_threshold` | `0.35` | Min similarity for a chunk (0–1) |
| `knowledge` | `enable_grounding_check` | `true` | Post-generation overlap check |
| `knowledge` | `grounding_min_overlap` | `0.30` | Min answer-token overlap (0–1) |
| `llm` | `temperature` | `0.3` | Sampling temperature (0–2) |
| `llm` | `max_tokens` | `512` | Max completion tokens |
| `llm` | `top_p` | `0.9` | Nucleus sampling |
| `llm` | `timeout_seconds` | `60` | LLM request timeout |
| `llm` | `max_retries` | `2` | SDK retries with exponential backoff |
| `llm` | `circuit_failure_threshold` | `5` | Consecutive failures that open the circuit |
| `llm` | `circuit_cooldown_seconds` | `30` | Time before a trial call is allowed |
| `embeddings` | `backend` | `openai_compat` | `openai_compat` \| `local` |
| `embeddings` | `model` | `bge-m3` (yaml) | Embedding model id (must match what was ingested) |
| `storage` | `lancedb_path` | `data/lancedb` | LanceDB directory |
| `storage` | `table_name` | `knowledge_base` | LanceDB table name |
| `storage` | `chunk_size` | `600` | Chars per chunk at ingest |
| `storage` | `chunk_overlap` | `100` | Chars of overlap between chunks |
| `whatsapp` | `api_version` | `v21.0` | Meta Graph API version |
| `whatsapp` | `graph_base_url` | `https://graph.facebook.com` | Graph endpoint (`WHATSAPP_GRAPH_BASE_URL`, e.g. a mock for load tests) |
| `whatsapp` | `timeout_seconds` | `15` | Per Graph API call |
| `whatsapp` | `max_retries` | `3` | Retries on 429 / 5xx / network errors |
| `redirects` | `enabled` | `true` | Master switch for redirects |
| `redirects` | `rules` | `[]` | Ordered keyword→url/whatsapp rules |
| `redirects` | `default_url` | `null` | Fallback site URL |
| `redirects` | `default_whatsapp` | `null` | Fallback vendor number |
| `tree` | `enabled` | `true` | Master switch for the conversation tree |
| `tree` | `path` | `tree.md` | File with the flow definitions |
| `tree` | `menu_keywords` | `menu, start, help` | Commands showing the flow list |
| `tree` | `redirect_message` | `Continue with a human agent here:` | Button text for a redirect without a `- message:` |
| `tree` | `max_steps` | `30` | Safety limit on executed steps per turn |
| `tree` | `menu_header` / `menu_footer` / `options_footer` | (es) | Customer-facing menu texts |
| `runtime` | `environment` | `development` | `development` \| `staging` \| `production` (`APP_ENV`) |
| `runtime` | `redis_url` / `redis_prefix` | `""` / `metabot` | Redis connection and key namespace |
| `queue` | `backend` | `memory` | `memory` \| `redis` |
| `queue` | `workers` | `2` | Parallel conversations per instance (= `OLLAMA_NUM_PARALLEL`) |
| `queue` | `max_pending` | `500` | Backlog limit (queued + in flight) |
| `queue` | `processing_timeout_seconds` | `120` | End-to-end budget per message |
| `queue` | `drain_timeout_seconds` | `25` | Graceful shutdown budget |
| `queue` | `ack_when_pending_over` | `10` | Backlog size that triggers `queued_message` (0 = off) |
| `queue` | `heartbeat_seconds` / `orphan_after_seconds` | `5` / `60` | Redis: worker liveness / requeue of a dead replica's work |
| `state` | `backend` | `memory` | `memory` \| `redis` |
| `state` | `conversation_ttl_seconds` | `1800` | Idle conversation expiry |
| `state` | `max_conversations` | `10000` | Memory backend LRU cap |
| `state` | `dedup_ttl_seconds` | `86400` | Message-id dedup window |
| `rate_limit` | `enabled` / `max_messages` / `window_seconds` | `true` / `20` / `60` | Per-phone fixed window |
| `rate_limit` | `max_input_chars` | `1000` | Longer inputs are truncated |

### 6.3 Environment variables (`.env`)

| Variable | Overrides | Notes |
|---|---|---|
| `WHATSAPP_ACCESS_TOKEN` | `whatsapp.access_token` | Meta permanent token |
| `WHATSAPP_PHONE_NUMBER_ID` | `whatsapp.phone_number_id` | Meta phone number id |
| `WHATSAPP_VERIFY_TOKEN` | `whatsapp.verify_token` | Your webhook verify secret |
| `LLM_BASE_URL` | `llm.base_url` | e.g. `http://localhost:8001/v1` (Docker: `http://ollama:11434/v1`) |
| `LLM_API_KEY` | `llm.api_key` | `EMPTY` for Ollama/vLLM local |
| `LLM_MODEL` | `llm.model` | `qwen3.5:4b` |
| `TEMPERATURE` | `llm.temperature` | float override |
| `EMBEDDING_BACKEND` | `embeddings.backend` | `openai_compat` \| `local` |
| `EMBEDDING_BASE_URL` | `embeddings.base_url` | defaults to `LLM_BASE_URL` when empty |
| `EMBEDDING_API_KEY` | `embeddings.api_key` | `EMPTY` |
| `EMBEDDING_MODEL` | `embeddings.model` | `bge-m3` |
| `CONFIG_PATH` | — | alternate YAML path |
| `LOG_LEVEL` | — | Python log level (default `INFO`) |
| `LOG_DIR` | — | folder for action logs (default `<project root>/logs`, container: `/app/logs`) |
| `LOG_RETENTION_DAYS` / `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT` | — | log purge by age / rotation size / backups (30 / 20 MB / 5) |
| `LOG_MESSAGE_CONTENT` | — | `1` logs message texts (debug only; default off) |
| `WHATSAPP_APP_SECRET` | `whatsapp.app_secret` | enables `X-Hub-Signature-256` validation (required in production) |
| `WHATSAPP_GRAPH_BASE_URL` | `whatsapp.graph_base_url` | Graph endpoint override |
| `APP_ENV` | `runtime.environment` | profile + fail-closed in `production` |
| `REDIS_URL` / `REDIS_PREFIX` | `runtime.redis_url` / `redis_prefix` | Redis backends |
| `QUEUE_BACKEND` / `QUEUE_WORKERS` / `QUEUE_MAX_PENDING` / `QUEUE_PROCESSING_TIMEOUT_SECONDS` | `queue.*` | queue sizing |
| `STATE_BACKEND` / `STATE_CONVERSATION_TTL_SECONDS` | `state.*` | state storage |
| `RATE_LIMIT_ENABLED` / `RATE_LIMIT_MAX_MESSAGES` / `RATE_LIMIT_WINDOW_SECONDS` | `rate_limit.*` | anti-abuse |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | `llm.*` | LLM resilience |
| `OLLAMA_KEEP_ALIVE` | — | Ollama model unload delay (compose, default `30m`) |
| `OLLAMA_NUM_PARALLEL` / `OLLAMA_MAX_QUEUE` | — | Ollama concurrency (compose, default `2` / `64`) |
| `APP_UID` / `APP_GID` | — | container user (build args, default `10001`) |
| `OLLAMA_VERSION`, `CADDY_VERSION`, `CLOUDFLARED_VERSION`, `REDIS_VERSION`, `PROMETHEUS_VERSION`, `GRAFANA_VERSION` | — | pinned image tags |

## 7. Module reference

### 7.1 `src/config.py`

Pydantic models mirror the YAML sections (`AppConfig`, `AgentConfig`,
`KnowledgeConfig`, `LLMConfig`, `EmbeddingsConfig`, `StorageConfig`,
`WhatsAppConfig`, `RedirectRule`, `RedirectConfig`, `TreeConfig`,
`ConversationLogConfig`, `RuntimeConfig`, `QueueConfig`, `StateConfig`,
`RateLimitConfig`, aggregated in `Settings`; `Settings.is_production`).
`load_settings(config_path=None)` is the single entry point used by the app,
the ingest CLI, and tests. `validate_settings(settings)` returns the list of
configuration problems (section 6.1).

### 7.2 `src/knowledge/chunker.py` — `TextChunker`

`TextChunker(chunk_size=600, overlap=100)`. Pure-Python, no external splitter.
`split_text(text) -> list[str]`:
- normalizes `\r\n` → `\n`;
- splits into paragraphs (blank lines) and sentences (`(?<=[.!?])\s+`);
- accumulates sentences into chunks bounded by `chunk_size`;
- seeds each next chunk with the previous chunk's `overlap` tail
  (word-boundary preserved);
- `_hard_split` brute-slices sentences longer than `chunk_size` with stride
  `chunk_size - overlap`.

Raises `ValueError` when `overlap >= chunk_size`.

### 7.3 `src/knowledge/embedding.py`

Protocol `EmbeddingProvider` (`embed(texts) -> list[list[float]]`, `dimension`).

- `OpenAICompatEmbedder` — `AsyncOpenAI(base_url, api_key)`, calls
  `embeddings.create`. Base URL resolution: `config.base_url or llm_base_url or
  "http://localhost:8001/v1"`. Dimension cached from first response; raises
  `RuntimeError` before the first call.
- `LocalEmbedder` — requires `backend == "local"`; lazy-loads a
  `SentenceTransformer`; runs CPU-bound encode in `asyncio.to_thread` with
  `normalize_embeddings=True`.
- `build_embedder(config, llm_base_url=None, timeout_seconds=30, max_retries=2)` —
  factory (the OpenAI-compatible client gets an explicit timeout and retries).

### 7.4 `src/knowledge/ingest.py`

- `SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".pdf", ".xlsx", ".xlsm"}`.
- `collect_files(path)` — single file or recursive `rglob`.
- `load_pdf` — `pypdf.PdfReader`, page failures skipped.
- `load_excel_rows(path)` — converts an `.xlsx` workbook into one text block
  per data row: the first non-empty row of each sheet is the column header
  (used to label cell values), each following non-empty row becomes
  `[sheet '<name>', row N] Col: value | ...`. Requires `openpyxl` (lazy
  import; raises `RuntimeError` with a hint if missing).
- `load_file_blocks(path)` — returns a single block for text/PDF, one block
  per row for Excel. `load_file` is the legacy single-string convenience.
- `chunk_documents(files, chunker)` — corrupt files logged and skipped; each
  chunk becomes `Document(text, source=f"{file}:{index}")`.
- `async ingest_documents(store, embedder, documents, batch_size=32)` — batches,
  embeds, stores via `vector_store.add`.

User-facing procedure for adding knowledge from Excel/Markdown/text:
`docs/ADD_KNOWLEDGE.md`.

### 7.5 `src/knowledge/vector_store.py`

`VectorStore(lancedb_path, table_name="knowledge_base")`:
- Embedded LanceDB (`lancedb.connect`), on-disk.
- `_ensure_table(dimension)` seeds a new table with one zero-vector row then
  deletes it (`delete("text = ''")`) to work around empty-table creation
  limits.
- `add(vectors, texts, metadatas) -> int` — metadata JSON-serialized.
- `search(query_vector, top_k)` — prefers `table.vector_search` (new API),
  falls back to `table.search`; converts distance→similarity
  `clamp(1.0 - distance, 0, 1)` (cosine distance ∈ [0,2] → similarity ∈ [0,1]).
- `search_async` — wraps in `asyncio.to_thread`.
- `count()` / `reset()` — used by health probe and `--reset`.

`SearchHit{text, similarity, metadata}`.

### 7.6 `src/llm/qwen.py`

`QwenClient(config: LLMConfig)` wraps `AsyncOpenAI`. `chat(system, messages,
*, temperature=None)` prepends the system message and returns
`choices[0].message.content` (stripped). The client is built with
`timeout=llm.timeout_seconds` and `max_retries=llm.max_retries` (SDK backoff on
408/429/5xx/network). Every call goes through a `CircuitBreaker`
(`src/utils/resilience.py`): after `circuit_failure_threshold` consecutive
failures it opens and `chat()` raises `CircuitOpenError` immediately for
`circuit_cooldown_seconds`, then lets one trial call through. Latency and
errors feed `metabot_llm_seconds`, `metabot_llm_errors_total` and
`metabot_circuit_open`. `ping()` (`/v1/models`) backs `/ready`.
Note: thinking models (`qwen3.5`)
return the reasoning in a separate `reasoning` field; `content` holds the
answer once generation completes within `max_tokens`.

### 7.7 `src/agent/prompts.py`

`SYSTEM_PROMPT` — `str.format` template with placeholders `{assistant_name}`,
`{company}`, `{language}`, `{fallback}`, `{refusal}`, `{context}`. The context
block is delimited by `=== KNOWLEDGE ===` / `=== END OF KNOWLEDGE ===`.
`build_system_prompt(settings, context)` fills it.

### 7.8 `src/whatsapp/meta.py`

`MetaWhatsAppClient(config)` builds the messages URL
`{graph_base_url}/{api_version}/{phone_number_id}/messages` and an
`httpx.AsyncClient` with a Bearer token.
- `verify_webhook(mode, verify_token, challenge)` — returns challenge when
  `mode == "subscribe"` and tokens match.
- `send_text(to, body)`, `send_cta_url(to, body, url, button_text="Open")`
  (interactive `open_link` button), `mark_read(message_id)` (best-effort).
- `_post` retries 429 / 5xx / transport errors up to `whatsapp.max_retries`
  (honours `Retry-After`, otherwise exponential backoff with jitter) and raises
  `MetaWhatsAppError` on non-retryable 4xx or when retries are exhausted.
  Errors are counted in `metabot_meta_send_errors_total{status}`; logged phone
  numbers are masked.

### 7.9 `src/tree/` — conversation tree
Markdown-defined scripted flows that run before the RAG answer.
- `parser.py` — `Flow`, `Step`, `Branch` dataclasses; `parse_tree(md_text)` and
  `load_tree(path)`. Grammar: `## <id>` headers, `Menu:` / `Keywords:` /
  `Description:` metadata, and steps `question:`, `option:`/`branch:`
  (attached to the preceding question), `answer:`, `message:` + `redirect:`,
  and `@label` markers (`- answer @label: text` is a label + answer shorthand).
- `engine.py` — `TreeEngine(flows, config)` keeps one `TreeState` per phone
  (`flow`, `question_index`, collected `fields`). `handle(phone, text)`
  continues an active session or starts a flow on a keyword/menu match and
  returns a `ReplyAction` (text, redirect with `https://wa.me/<number>`) or
  `None` to fall back to RAG. Option replies match by number or whole word
  (so "a" does not match "banana"); unknown options re-ask the question unless
  a `*` route exists.
- The engine is constructed lazily in `WhatsAppOrchestrator._build_tree()` and
  consulted after the redirect guardrails but before retrieval. Sessions are
  per-phone; between messages they live in the state store
  (`export_session` / `import_session`, TTL `state.conversation_ttl_seconds`),
  so they survive restarts and are shared across replicas with the Redis
  backend. A stored session whose flow no longer exists in `tree.md` is
  discarded. Menu texts come from `tree.menu_header`, `menu_footer`,
  `options_footer`.
- Flows live in `tree.md` (mounted live from the compose root). Enable/disable
  with `tree.enabled` in `config/config.yaml`.

### 7.10 `src/utils/logging.py` — action logs (rotated, PII-safe)

`setup_logging()` configures the root logger with a console handler and a
`RotatingFileHandler` named `wa_ollama_logs_<YYYYMMDD_HHMMSS_uuuuuu>.txt` in
`LOG_DIR` (default `<project root>/logs`, container `/app/logs`). Each run gets
its own file, which rotates at `LOG_MAX_BYTES` (20 MB) keeping
`LOG_BACKUP_COUNT` (5) backups; at startup, files older than
`LOG_RETENTION_DAYS` (30) are purged. `logs/` is bind-mounted and excluded from
the image. `LOG_LEVEL` controls verbosity.

Privacy (Ley 1581 de 2012): phone numbers are always masked
(`src/utils/pii.py:mask_phone`, e.g. `5730*****567`) and message texts are
logged as `<N chars>` unless `LOG_MESSAGE_CONTENT=1` (local debugging only).

Action records are emitted at the decision points:

- `src/main.py:_ingest` — `Inbound | phone=… msg_id=… type=… text=…`,
  `Duplicate delivery ignored`, `Rate limited`, `Queue rejected message`
- `src/pipeline.py` — `Action computed | phone=… msg_id=… type=… outcome=… reply=…`
- `src/tree/engine.py` — `Tree action | phone=… flow=… started|continued`
- `src/dispatch/*` — dispatcher start, drain and orphan requeue events
- `scripts/ingest_cli.py` — `Ingest action | source=… reset=…`

Uvicorn access/error records and httpx API calls propagate to the same file,
so every request and every Meta API round-trip is auditable. The suite covers
this in `tests/test_logging.py`.

## 8. HTTP API

| Route | Method | Purpose | Notes |
|---|---|---|---|
| `/webhook` | GET | Meta webhook verification | echoes `hub.challenge`; 403 on failure |
| `/webhook` | POST | Inbound WhatsApp payload | ack `{"status":"received"}`; 403 if `X-Hub-Signature-256` invalid (when app secret set, mandatory in production); 400 on bad JSON/payload; messages are deduplicated, rate limited and enqueued |
| `/health` | GET | Liveness + chunk count | `{"status":"ok","chunks":N}` (Docker healthcheck) |
| `/ready` | GET | Readiness | 200 / 503 with `checks` (queue, knowledge, llm, llm_circuit_closed, redis, whatsapp_configured in production) and queue depth |
| `/metrics` | GET | Prometheus metrics | internal only (blocked by Caddy) |
| `/docs`, `/openapi.json` | GET | API docs | disabled when `APP_ENV=production` |

`extract_text` (`src/main.py`) supports `text` messages, `interactive`
`button_reply`/`list_reply` titles and template quick-reply `button` texts.
`image`, `audio`, `voice`, `video`, `document`, `sticker`, `location` and
`contacts` get `agent.unsupported_message`; reactions and system messages are
ignored.

The exposed `8001` port is the LLM gateway, not the chatbot:

All host ports are bound to `127.0.0.1`:

| URL | Service |
|---|---|
| `http://localhost:8000` | chatbot (webhook, health, ready, metrics) |
| `http://localhost:8001/v1/models` | Ollama models |
| `http://localhost:8001/v1/chat/completions` | Ollama chat |
| `http://localhost:8001/v1/embeddings` | Ollama embeddings |

## 9. Data model & storage

- **LanceDB** (`data/lancedb`, table `knowledge_base`): schema
  `vector: float[]`, `text: str`, `metadata: str` (JSON). Created lazily at
  ingest or first app start; `count()` at startup logs the chunk total.
- **Conversation state** (`src/state/`): one snapshot per phone
  `{"history": [{"role","content"}…], "tree": {flow, question_index, fields} | null}`,
  trimmed to `agent.history_size` turns and expiring after
  `state.conversation_ttl_seconds` of inactivity. `memory` backend: bounded LRU
  (`state.max_conversations`) in the process. `redis` backend: key
  `<prefix>:conv:<phone>` with TTL, shared by all replicas and persisted.
- **Dedup / rate limit**: `<prefix>:seen:<msg_id>` (TTL `dedup_ttl_seconds`) and
  `<prefix>:rl:<phone>:<window>` counters (memory equivalents in-process).
- **Queue (redis backend)**: keys under `<prefix>:q:` — see section 15.
- **Conversation registry** (`src/registry/`): SQLite or Google Sheets rows
  written in batches by a single writer — see `docs/CONVERSATION_REGISTRY.md`.

## 10. Embedding backends

- **`openai_compat` (default)** — served by Ollama (`bge-m3`) at
  `http://ollama:11434/v1/embeddings` inside Docker. Dimension is inferred from
  the first response.
- **`local`** — `sentence-transformers` on the host/container. Install
  `requirements-local.txt`. `model` is an HF id (default
  `BAAI/bge-small-en-v1.5`).

**Important**: the embedding model determines the vector space. If you change
`EMBEDDING_MODEL`, you must re-ingest the knowledge base
(`ingest_cli.py --reset`), or retrieval will mix incompatible vector spaces.

## 11. Deployment

### 11.1 Default stack (`docker-compose.yml`)

| Service | Image | Ports | Volumes |
|---|---|---|---|
| `chatbot` | built from `Dockerfile` (python:3.11-slim, user `app` UID 10001) | `127.0.0.1:8000:8000` | `./data`, `./logs`, `./knowledge_base`; `./config`, `./scripts`, `./tree.md`, `./deploy/credentials` read-only |
| `ollama` | `ollama/ollama:${OLLAMA_VERSION:-0.34.3}` | `127.0.0.1:8001:11434` | `./ollama-models:/root/.ollama` |

- `chatbot` reads `.env` via `env_file`; `LLM_BASE_URL` and
  `EMBEDDING_BASE_URL` are overridden to `http://ollama:11434/v1` so the app
  always talks to the internal Ollama service.
- `chatbot` has a Docker HEALTHCHECK against `/health` (`curl -fsS`).
- The `ollama` entrypoint runs `ollama serve`, waits, then
  `ollama pull qwen3.5:4b && ollama pull bge-m3` (no-op once cached), then
  `wait`. Healthcheck: `ollama list`.
- `OLLAMA_KEEP_ALIVE=30m` keeps the model loaded between messages;
  `OLLAMA_NUM_PARALLEL` (2) and `OLLAMA_MAX_QUEUE` (64) set its concurrency.
  Keep `QUEUE_WORKERS` × replicas ≤ `OLLAMA_NUM_PARALLEL`.
- `chatbot` has `stop_grace_period: 40s` so SIGTERM drains the queue
  (uvicorn `--timeout-graceful-shutdown 30`).
- On Linux hosts `./data` and `./logs` must be writable by UID 10001
  (`sudo chown -R 10001:10001 data logs`, or `APP_UID`/`APP_GID` build args).
- Both services use `restart: unless-stopped` and `depends_on` for ordering.

Overlays (combine with `-f`):

| File | Adds |
|---|---|
| `docker-compose.redis.yml` | `redis` (AOF, no host port) + `QUEUE_BACKEND=redis`, `STATE_BACKEND=redis` |
| `docker-compose.caddy.yml` | Caddy reverse proxy (only `/webhook` and `/health` public) |
| `docker-compose.tunnel.yml` | Cloudflare Tunnel → `caddy:8080` (`CLOUDFLARED_CREDENTIALS_FILE`) |
| `docker-compose.monitoring.yml` | Prometheus (`127.0.0.1:9090`) + Grafana (`127.0.0.1:3000`) |

### 11.2 GPU-only alternative (`qwen-service/`)

vLLM stack for NVIDIA hosts (larger Qwen 3.5 MoE models). Build arg
`VLLM_DEVICE=gpu|cpu`; GPU compose reserves devices with
`deploy: resources: reservations: devices`. See `qwen-service/runDockerFile.txt`
for the full ops guide. Not wired into the root stack — running it requires
pointing `.env` `LLM_BASE_URL`/`LLM_MODEL` at it manually.

### 11.3 Ports and networking

- Host `127.0.0.1:8000` → chatbot; host `127.0.0.1:8001` → Ollama (no
  authentication, so never public). `.env.example` keeps
  `LLM_BASE_URL=http://localhost:8001/v1` for host-run CLIs.
- Inside Docker the chatbot reaches Ollama by service name (`ollama:11434`),
  so the internal endpoint differs from the host-mapped one — this is
  intentional.
- Optional Caddy overlay (`docker-compose.caddy.yml`): host `127.0.0.1:8080`
  → Caddy → chatbot (service DNS `chatbot:8000`, round-robin across replicas,
  active health checks). Only `/webhook` and `/health` are proxied; everything
  else answers 404. Set `CADDY_DOMAIN` in `.env` to switch to HTTPS on 80/443.
  The overlay is not part of the default stack — it is started with
  `docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d`.
  Webhook verification through the proxy:
  `powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1`.

## 12. Testing

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

CI (`.github/workflows/ci.yml`): tests with a Redis service, Gitleaks secrets
scan, compose validation and image build. The workflow runs with a
least-privilege `GITHUB_TOKEN` (`contents: read`, `pull-requests: read`; the
latter is needed by Gitleaks to list a PR's commits).

| File | Coverage |
|---|---|
| `test_config.py` | defaults, YAML round-trip, env overrides, validation |
| `test_chunker.py` | size bounds, empty text, overlap, hard split, invariant |
| `test_guardrails.py` | forbidden subject, redirects, threshold, grounding |
| `test_ingest.py` | full ingest pipeline with a fake embedder + tmp LanceDB |
| `test_logging.py` | persistent log-file naming, content, error tracebacks |
| `test_orchestrator.py` | end-to-end RAG + tree flows with real LanceDB + fake embedder/LLM |
| `test_tree.py` | tree parser grammar + engine state transitions (menus, options, branches, redirects) |
| `test_webhook.py` | `/health`, webhook verify (ok/fail), POST ack, signatures |
| `test_registry.py` | SQLite / Google Sheets backends, contacts enrichment |
| `test_dispatch_local.py` | per-phone order, bounded parallelism, fairness, backpressure, drain |
| `test_dispatch_redis.py` | two replicas sharing a queue, global backpressure, orphan requeue, no replay, Redis state/dedup/rate limit (real Redis; skipped if unavailable) |
| `test_app_pipeline.py` | end to end webhook → queue → reply: concurrency, dedup, busy/rate-limit/unsupported notices, truncation, timeout and circuit fallbacks, drain, `/ready`, `/metrics`, production fail-closed |
| `test_resilience_and_state.py` | circuit breaker, Meta retries, TTL/LRU state, orchestrator state persistence, PII masking, config validation/profiles, registry batching |

Tests use `FakeEmbedder` (deterministic 3-dim vectors) and `FakeQwen`, so the
suite runs without an LLM or WhatsApp credentials. The RAG tests disable
`tree.enabled` to isolate the retrieval pipeline from the flow engine. App
tests inject fakes through `AppComponents` (`tests/conftest.py`).

Load testing: `scripts/mock_services.py` emulates the Meta Graph API and an
OpenAI-compatible LLM with configurable latency and parallelism;
`scripts/load_test.py` sends signed webhooks from N concurrent customers and
waits until `/metrics` reports every message processed (see README → Tests).

## 13. Extending the project

- **New LLM provider** — `QwenClient` already talks to any OpenAI-compatible
  endpoint; point `LLM_BASE_URL` at it. For a native SDK, add a provider
  behind the same `chat()` contract in `src/llm/`.
- **New embedding model** — change `EMBEDDING_MODEL` (and the Ollama pull list
  in compose) and re-ingest with `--reset`.
- **New document types** — add an extension to `SUPPORTED_EXTENSIONS` and a
  loader in `src/knowledge/ingest.py`.
- **New guardrails** — add a method to `Guardrails` and call it in the
  `handle_message` pipeline.
- **Persistent conversations / horizontal scaling** — already supported:
  `docker-compose.redis.yml` + more `chatbot` replicas (see OPERATIONS.md).
- **New state or queue backend** — implement the `StateStore` / `Deduplicator`
  / `RateLimiter` protocols (`src/state/base.py`) or the `Dispatcher` ABC
  (`src/dispatch/base.py`) and register it in the factory.
- **New bot / business process** — a separate deployment with its own `.env`,
  `config.<APP_ENV>.yaml`, `tree.md`, `knowledge_base/` and `REDIS_PREFIX`.
- **Tuning without code** — everything in `config.yaml` plus the env overrides
  in section 6.3.

## 14. Security considerations

- **Secrets** — `.env` files are git-ignored. Never commit `WHATSAPP_ACCESS_TOKEN`
  or `HF_TOKEN`. The compose file passes secrets only through `env_file`.
- **Webhook verification** — GET `/webhook` checks `hub.verify_token` before
  echoing the challenge; invalid requests get 403.
- **Meta signature validation** — when `WHATSAPP_APP_SECRET` is set, the POST
  `/webhook` handler reads the raw body and verifies `X-Hub-Signature-256`
  (`HMAC-SHA256(body, app_secret)`, constant-time compare) before any
  processing; invalid or missing signatures get 403. Without the secret the
  app refuses to start in production (`APP_ENV=production`); in development it
  logs a warning once and accepts payloads (OK for the test number).
  See `src/whatsapp/meta.py:verify_signature` and `deploy/README.md`.
- **Fail-closed configuration** — see section 6.1.
- **Network surface** — every host port bound to `127.0.0.1`; Redis has no
  host port; Caddy exposes only `/webhook` and `/health`.
- **Container hardening** — non-root user, pinned image tags, read-only
  mounts for config, scripts, tree and credentials.
- **Repository hygiene** — `.gitignore` covers `.env*`, credentials, keys and
  `ollama-models/` (which holds Ollama's private key); CI runs Gitleaks.
- **Personal data** — masked phones, no message texts in logs by default,
  rotation and age-based purge. Google Sheets rows are written with
  `value_input_option="RAW"` so customer text cannot inject formulas.
- **HTTPS** — Meta requires a public HTTPS URL for webhooks. Put the stack
  behind a reverse proxy (Caddy/nginx) terminating TLS and expose only the
  webhook; see `deploy/Caddyfile` and `deploy/README.md`.
- **Rate limiting / abuse** — per-phone fixed window (`rate_limit.*`) with a
  single notice per window, input truncation (`max_input_chars`), global
  backpressure (`queue.max_pending`) and a 1 MB body limit at Caddy.
- **LLM prompt injection** — the system prompt instructs the model to answer
  only from the provided knowledge, but retrieval can surface user-controlled
  text. The grounding check mitigates off-context answers; review ingested
  documents for hostile content.

## 15. Queue, state and resilience

**Design — mailbox per phone** (`src/dispatch/base.py`). Each phone number has
its own FIFO mailbox; a "ready" line holds phones with pending work. A worker
takes a phone, processes **one** message, and puts the phone back at the end of
the line if more are pending. Consequences:

- strict order within a conversation and no concurrent processing of one phone
  (no races on history or tree session);
- parallelism across conversations bounded by `queue.workers`;
- fairness: a chatty customer cannot starve the others;
- `queue.max_pending` bounds the backlog (queued + in flight); above it
  `submit()` rejects and the customer gets `agent.busy_message`.

| | `LocalDispatcher` (`memory`) | `RedisDispatcher` (`redis`) |
|---|---|---|
| Infrastructure | none | Redis 8 (`docker-compose.redis.yml`) |
| Replicas | 1 | N (shared queue) |
| Restart / deploy | graceful drain on SIGTERM; hard crash loses backlog | backlog persisted (AOF) |
| Delivery | at-most-once | at-least-once (+ `done:<msg_id>` marker to skip replays) |

Redis keys (`<prefix>:q:`): `mbox:<phone>` (list), `sched:<phone>`, `ready`
(list, consumed with `BLMOVE` into `proc:<worker>#<slot>`), `workers` (zset of
heartbeats), `pending` (counter), `done:<msg_id>`. Submit and finish are atomic
Lua scripts. A message leaves its mailbox only after the handler returns; if a
replica dies, its heartbeat goes stale and any replica moves its owned phones
back to `ready` after `queue.orphan_after_seconds`.

**Request path** (`src/main.py:_ingest`): signature → JSON → per message:
`dedup.first_seen(msg_id)` → `limiter.hit(phone)` → truncate → `submit()`.
Rejections and notices are sent as FastAPI background tasks after the 200.

**Resilience summary**

| Failure | Behaviour |
|---|---|
| Meta redelivers a message | dropped by `msg_id` dedup (`metabot_messages_duplicate_total`) |
| Burst above capacity | backlog grows up to `max_pending`, then `busy_message` |
| LLM slow | per-call timeout + SDK retries; per-message `processing_timeout_seconds` → fallback |
| LLM down | circuit breaker opens → instant fallback, `/ready` = 503, alert |
| Meta 429 / 5xx | retries with `Retry-After` / backoff |
| Registry backend down | rows dropped and counted (`metabot_registry_dropped_total`), chat unaffected |
| Replica crash (redis) | work requeued after `orphan_after_seconds` |

## 16. Observability

- `/ready` — readiness for load balancers and deploy checks (section 8).
- `/metrics` — Prometheus exposition (`src/utils/metrics.py`):
  `metabot_messages_received_total{kind}`, `metabot_messages_duplicate_total`,
  `metabot_messages_rejected_total{reason}`,
  `metabot_messages_processed_total{action,outcome}`,
  `metabot_processing_seconds`, `metabot_queue_wait_seconds`,
  `metabot_queue_pending`, `metabot_queue_inflight`, `metabot_llm_seconds`,
  `metabot_llm_errors_total{reason}`, `metabot_circuit_open{name}`,
  `metabot_meta_send_errors_total{status}`, `metabot_registry_dropped_total`.
- `docker-compose.monitoring.yml` — Prometheus discovers every `chatbot`
  replica by DNS; alert rules in `deploy/monitoring/alerts.yml`; Grafana
  dashboard "Meta Bot - Operación" provisioned from
  `deploy/monitoring/grafana/`.
