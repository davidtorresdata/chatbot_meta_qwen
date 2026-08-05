# Technical Documentation — WhatsApp Chatbot (Qwen + LanceDB)

Developer-oriented reference for the WhatsApp RAG chatbot. For operators and
end users, see the [User Manual](USER_MANUAL.md).

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
│ src/main.py — FastAPI app                                          │
│  • /webhook  GET   verify_webhook() → echoes hub.challenge         │
│  • /webhook  POST  parse payload → background task per message     │
│  • /health   GET   {"status":"ok","chunks":N}                      │
└──────────────┬────────────────────────────────────────────────────┘
               ▼  _handle_message (BackgroundTasks)
┌───────────────────────────────────────────────────────────────────┐
│ src/agent/orchestrator.py — WhatsAppOrchestrator                   │
│  1. Guardrails.forbidden_subject()   → refusal if "how were you    │
│                                          built / your prompt..."   │
│  2. Guardrails.match_redirect()      → CTA button (url / wa.me)    │
│  3. embed question (embedder)                                      │
│  4. vector_store.search_async()      → top-k hits (LanceDB)        │
│  5. Guardrails.retrieval_ok()        → similarity threshold gate   │
│  6. build_system_prompt() + conversation history                   │
│  7. qwen.chat()                      → answer from context only    │
│  8. Guardrails.is_grounded()         → lexical overlap check       │
└──────────────┬────────────────────────────────────────────────────┘
               ▼  ReplyAction{type, message, url, whatsapp}
┌───────────────────────────────────────────────────────────────────┐
│ src/whatsapp/meta.py — MetaWhatsAppClient                          │
│  • send_text()        • send_cta_url() (open_link button)          │
│  • mark_read()                                                     │
└────────────────────────────────────────────────────────────────────┘
               │
               ▼  POST https://graph.facebook.com/v21.0/<phone_number_id>/messages
        WhatsApp user receives the reply
```

Component responsibilities:

| Component | Responsibility |
|---|---|
| `src/main.py` | HTTP surface: webhook verification, inbound ingestion, health probe |
| `src/agent/orchestrator.py` | End-to-end RAG pipeline, per-phone conversation state |
| `src/agent/guardrails.py` | Forbidden-subject, redirect-intent, retrieval-threshold, grounding checks |
| `src/agent/prompts.py` | System-prompt template |
| `src/knowledge/vector_store.py` | LanceDB CRUD + cosine-similarity search |
| `src/knowledge/embedding.py` | Pluggable embedders (OpenAI-compatible / local sentence-transformers) |
| `src/knowledge/chunker.py` | Pure-Python overlapping text splitter |
| `src/knowledge/ingest.py` | File loading (.txt/.md/.pdf) + ingest pipeline |
| `src/llm/qwen.py` | Async OpenAI-compatible chat client |
| `src/whatsapp/meta.py` | Meta Cloud API client (send, redirects, read receipts, webhook verify) |
| `src/config.py` | Pydantic settings: `config.yaml` + `.env` overrides |

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
| Tests | `pytest` + `fastapi.testclient` |
| Containerization | `Dockerfile` + `docker-compose.yml` (chatbot + ollama) |

## 4. Repository layout

```
├── config/config.yaml         # tunable settings (safe to edit in place)
├── knowledge_base/            # RAG source documents (.txt/.md/.pdf/.xlsx)
├── data/lancedb/              # LanceDB knowledge base (created on ingest)
├── logs/                      # persistent action logs (wa_ollama_logs_*.txt)
├── tree.md                    # conversation-tree flows (safe to edit in place)
├── ollama-models/             # Ollama model cache (qwen3.5:4b, bge-m3)
├── scripts/ingest_cli.py      # CLI to chunk + embed + store documents
├── src/
│   ├── main.py                # FastAPI app + routes
│   ├── config.py              # settings loader
│   ├── agent/                 # orchestrator, guardrails, prompts
│   ├── knowledge/             # chunker, embedding, ingest, vector_store
│   ├── llm/                   # qwen client
│   ├── tree/                  # conversation-tree parser + engine
│   ├── utils/                 # logging setup
│   └── whatsapp/              # meta client
├── tests/                     # pytest suite
├── qwen-service/              # GPU-only vLLM serving stack (alternative)
├── Dockerfile
├── docker-compose.yml         # chatbot + ollama
├── requirements.txt           # runtime deps
└── requirements-local.txt     # optional offline-embedding deps
```

## 5. Message processing pipeline

Entry point is `src/main.py:_handle_message` (`src/main.py:100`), which runs in
a FastAPI `BackgroundTasks` slot so Meta receives an immediate `{"status":
"received"}` acknowledgement. The pipeline is `WhatsAppOrchestrator.handle_message`
(`src/agent/orchestrator.py:71`):

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

`ReplyAction` (`src/agent/orchestrator.py:31`) is a dataclass: `type`
(`"text" | "redirect" | "fallback"`), `message`, optional `url`/`whatsapp`.
`_send_action` (`src/main.py:120`) maps it to a Meta API call.

## 6. Configuration system

### 6.1 Precedence

`load_settings()` (`src/config.py:139`):

1. Load `.env` from the project root (via `python-dotenv`).
2. Load `config/config.yaml` (or `CONFIG_PATH` env, or explicit arg).
3. Validate with Pydantic `Settings.model_validate`.
4. Overlay environment variables (`_apply_env_overrides`, `src/config.py:110`).

Env vars win over the YAML. Both are optional — every field has a default.

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
| `knowledge` | `top_k` | `5` | Chunks retrieved per question (1–50) |
| `knowledge` | `score_threshold` | `0.35` | Min similarity for a chunk (0–1) |
| `knowledge` | `enable_grounding_check` | `true` | Post-generation overlap check |
| `knowledge` | `grounding_min_overlap` | `0.30` | Min answer-token overlap (0–1) |
| `llm` | `temperature` | `0.3` | Sampling temperature (0–2) |
| `llm` | `max_tokens` | `512` | Max completion tokens |
| `llm` | `top_p` | `0.9` | Nucleus sampling |
| `llm` | `timeout_seconds` | `90` | LLM request timeout |
| `embeddings` | `backend` | `openai_compat` | `openai_compat` \| `local` |
| `embeddings` | `model` | `BAAI/bge-small-en-v1.5` | Embedding model id |
| `storage` | `lancedb_path` | `data/lancedb` | LanceDB directory |
| `storage` | `table_name` | `knowledge_base` | LanceDB table name |
| `storage` | `chunk_size` | `600` | Chars per chunk at ingest |
| `storage` | `chunk_overlap` | `100` | Chars of overlap between chunks |
| `whatsapp` | `api_version` | `v21.0` | Meta Graph API version |
| `whatsapp` | `graph_base_url` | `https://graph.facebook.com` | Graph endpoint |
| `redirects` | `enabled` | `true` | Master switch for redirects |
| `redirects` | `rules` | `[]` | Ordered keyword→url/whatsapp rules |
| `redirects` | `default_url` | `null` | Fallback site URL |
| `redirects` | `default_whatsapp` | `null` | Fallback vendor number |
| `tree` | `enabled` | `true` | Master switch for the conversation tree |
| `tree` | `path` | `tree.md` | File with the flow definitions |
| `tree` | `menu_keywords` | `menu, start, help` | Commands showing the flow list |
| `tree` | `redirect_message` | `Continue with a human agent here:` | Button text for a redirect without a `- message:` |
| `tree` | `max_steps` | `30` | Safety limit on executed steps per turn |

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
| `OLLAMA_KEEP_ALIVE` | — | Ollama model unload delay (compose, default `30m`) |

## 7. Module reference

### 7.1 `src/config.py`

Pydantic models mirror the YAML sections (`AppConfig`, `AgentConfig`,
`KnowledgeConfig`, `LLMConfig`, `EmbeddingsConfig`, `StorageConfig`,
`WhatsAppConfig`, `RedirectRule`, `RedirectConfig`, aggregated in `Settings`).
`load_settings(config_path=None)` is the single entry point used by the app,
the ingest CLI, and tests.

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
- `build_embedder(config, llm_base_url=None)` — factory.

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
`choices[0].message.content` (stripped). Note: thinking models (`qwen3.5`)
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
- `_post` raises `MetaWhatsAppError` on HTTP ≥ 400.

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
  per-phone and in-memory, cleared on flow completion or `reset_conversation`.
- Flows live in `tree.md` (mounted live from the compose root). Enable/disable
  with `tree.enabled` in `config/config.yaml`.

### 7.10 `src/utils/logging.py` — persistent action logs

`setup_logging()` configures the root logger with a console handler and a
`FileHandler` named `wa_ollama_logs_<YYYYMMDD_HHMMSS_uuuuuu>.txt` in `LOG_DIR`
(default `<project root>/logs`, container `/app/logs`). The timestamp is fixed
at startup so each run gets its own file and no file is ever deleted. Logs
survive restarts because `logs/` is bind-mounted (`./logs:/app/logs`) and
excluded from the Docker image. `LOG_LEVEL` controls verbosity.

Action records are emitted at the decision points:

- `src/main.py:webhook_receive` — `Inbound action | phone=… msg_id=… text=…`
- `src/main.py:_handle_message` — `Action computed | phone=… type=… message=… url=…`
- `src/main.py:_send_action` — `Action sent | phone=… kind=text|redirect`
- `src/tree/engine.py` — `Tree action | phone=… flow=… started|continued`
- `scripts/ingest_cli.py` — `Ingest action | source=… reset=…`

Uvicorn access/error records and httpx API calls propagate to the same file,
so every request and every Meta API round-trip is auditable. The suite covers
this in `tests/test_logging.py`.

## 8. HTTP API

| Route | Method | Purpose | Notes |
|---|---|---|---|
| `/webhook` | GET | Meta webhook verification | echoes `hub.challenge`; 403 on failure |
| `/webhook` | POST | Inbound WhatsApp payload | ack `{"status":"received"}`; 403 if `X-Hub-Signature-256` invalid (when app secret set); 400 on bad JSON; per-message background task |
| `/health` | GET | Liveness + chunk count | `{"status":"ok","chunks":N}` |

`extract_text` (`src/main.py:87`) supports `text` messages and `interactive`
`button_reply`/`list_reply` titles.

The exposed `8001` port is the LLM gateway, not the chatbot:

| URL | Service |
|---|---|
| `http://localhost:8000` | chatbot (webhook + health) |
| `http://localhost:8001/v1/models` | Ollama models |
| `http://localhost:8001/v1/chat/completions` | Ollama chat |
| `http://localhost:8001/v1/embeddings` | Ollama embeddings |

## 9. Data model & storage

- **LanceDB** (`data/lancedb`, table `knowledge_base`): schema
  `vector: float[]`, `text: str`, `metadata: str` (JSON). Created lazily at
  ingest or first app start; `count()` at startup logs the chunk total.
- **Conversations** (`src/agent/orchestrator.py:39`): in-memory `dict[phone →
  Conversation]`, capped at `max_conversations` (default 10 000). Each
  `Conversation` keeps `history` as a list of `{"role","content"}` maps, trimmed
  to `history_size` turns. **Not persisted** — restarting the container loses
  history. Extend by swapping for Redis in `orchestrator.py`.

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
| `chatbot` | built from `Dockerfile` (python:3.11-slim) | `8000:8000` | `./data`, `./knowledge_base`, `./config`, `./scripts` |
| `ollama` | `ollama/ollama` | `8001:11434` | `./ollama-models:/root/.ollama` |

- `chatbot` reads `.env` via `env_file`; `LLM_BASE_URL` and
  `EMBEDDING_BASE_URL` are overridden to `http://ollama:11434/v1` so the app
  always talks to the internal Ollama service.
- `chatbot` has a Docker HEALTHCHECK against `/health` (`curl -fsS`).
- The `ollama` entrypoint runs `ollama serve`, waits, then
  `ollama pull qwen3.5:4b && ollama pull bge-m3` (no-op once cached), then
  `wait`. Healthcheck: `ollama list`.
- `OLLAMA_KEEP_ALIVE=30m` keeps the model loaded between messages.
- Both services use `restart: unless-stopped` and `depends_on` for ordering.

### 11.2 GPU-only alternative (`qwen-service/`)

vLLM stack for NVIDIA hosts (larger Qwen 3.5 MoE models). Build arg
`VLLM_DEVICE=gpu|cpu`; GPU compose reserves devices with
`deploy: resources: reservations: devices`. See `qwen-service/runDockerFile.txt`
for the full ops guide. Not wired into the root stack — running it requires
pointing `.env` `LLM_BASE_URL`/`LLM_MODEL` at it manually.

### 11.3 Ports and networking

- Host `8000` → chatbot; host `8001` → Ollama. Compose override in
  `.env.example` keeps `LLM_BASE_URL=http://localhost:8001/v1` for local runs.
- Inside Docker the chatbot reaches Ollama by service name (`ollama:11434`),
  so the internal endpoint differs from the host-mapped one — this is
  intentional.
- Optional Caddy overlay (`docker-compose.caddy.yml`): host `8080` → Caddy
  → chatbot (service name `whatsapp-qwen-chatbot:8000`). Default site address
  is `http://localhost:8080`; set `CADDY_DOMAIN` in `.env` to switch to HTTPS
  on 80/443 (uncomment those mappings in the overlay). The overlay is not part
  of the default stack — it is started with
  `docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d`.
  Webhook verification through the proxy:
  `powershell -ExecutionPolicy Bypass -File deploy\test-webhook.ps1`.

## 12. Testing

```bash
python -m pytest tests -q
```

| File | Coverage |
|---|---|
| `test_config.py` | defaults, YAML round-trip, env overrides, validation |
| `test_chunker.py` | size bounds, empty text, overlap, hard split, invariant |
| `test_guardrails.py` | forbidden subject, redirects, threshold, grounding |
| `test_ingest.py` | full ingest pipeline with a fake embedder + tmp LanceDB |
| `test_logging.py` | persistent log-file naming, content, error tracebacks |
| `test_orchestrator.py` | end-to-end RAG + tree flows with real LanceDB + fake embedder/LLM |
| `test_tree.py` | tree parser grammar + engine state transitions (menus, options, branches, redirects) |
| `test_webhook.py` | `/health`, webhook verify (ok/fail), POST ack |

Tests use `FakeEmbedder` (deterministic 3-dim vectors) and `FakeQwen`, so the
suite runs without an LLM or WhatsApp credentials. The RAG tests disable
`tree.enabled` to isolate the retrieval pipeline from the flow engine.

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
- **Persistent conversations** — replace the in-memory dict in
  `WhatsAppOrchestrator` with a Redis-backed store (same interface).
- **Horizontal scaling** — keep `Conversation` out of process memory (see
  above); the rest (webhook → background task) is stateless.
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
  handler logs a warning and accepts payloads (OK for the test number).
  See `src/whatsapp/meta.py:verify_signature` and `deploy/README.md`.
- **HTTPS** — Meta requires a public HTTPS URL for webhooks. Put the stack
  behind a reverse proxy (Caddy/nginx) terminating TLS and expose only the
  webhook; see `deploy/Caddyfile` and `deploy/README.md`.
- **Rate limiting / abuse** — no inbound rate limiting is implemented; add one
  at the proxy layer if the bot is public.
- **LLM prompt injection** — the system prompt instructs the model to answer
  only from the provided knowledge, but retrieval can surface user-controlled
  text. The grounding check mitigates off-context answers; review ingested
  documents for hostile content.
