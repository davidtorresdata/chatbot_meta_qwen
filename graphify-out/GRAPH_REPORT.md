# Graph Report - metabot  (2026-09-26)

## Corpus Check
- Corpus is ~48,266 words - fits in a single context window. You may not need a graph.

## Summary
- 824 nodes · 1947 edges · 44 communities (28 shown, 16 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 162 edges (avg confidence: 0.92)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Arranque y registro de conversaciones
- Árbol de conversación
- RAG e ingesta de conocimiento
- Scripts CLI y prueba de carga
- Fixtures de pruebas y errores de config
- Configuración y despliegue
- Orquestador de conversación
- Guardrails anti-alucinación
- Cola local (LocalDispatcher)
- Contrato de la cola (buzón por teléfono)
- App FastAPI y privacidad de datos
- Modelos de configuración
- Cliente Meta y reintentos
- Embeddings
- Cliente LLM Qwen
- Validación de configuración
- Contratos de estado (dedup, rate limit)
- Pruebas con Redis real
- Estado en Redis y rate limit
- Carga de settings y OAuth Sheets
- Estado en memoria
- Cola Redis (RedisDispatcher)
- Pipeline de procesamiento
- Logging rotado
- Dedup y límite en memoria
- Servicio de registro
- Monitoreo Prometheus/Grafana
- Paquete registry
- MessageProcessor
- Simulador de chat CLI
- Workers de cola local
- Servicio vLLM
- Pendiente: uv.lock
- Metadatos del proyecto
- Dependencias

## God Nodes (most connected - your core abstractions)
1. `Settings` - 49 edges
2. `RedisDispatcher` - 38 edges
3. `Technical documentation` - 37 edges
4. `InboundMessage` - 36 edges
5. `WhatsAppOrchestrator` - 35 edges
6. `LocalDispatcher` - 34 edges
7. `create_app()` - 31 edges
8. `TreeEngine` - 31 edges
9. `load_settings()` - 29 edges
10. `VectorStore` - 27 edges

## Surprising Connections (you probably didn't know these)
- `load_settings()` --implements--> `Config profiles config.<APP_ENV>.yaml`  [INFERRED]
  src/config.py → docs/TECHNICAL_DOCUMENTATION.md
- `RedisDispatcher` --implements--> `Orphan requeue of dead replicas`  [INFERRED]
  src/dispatch/redis_dispatcher.py → docs/TECHNICAL_DOCUMENTATION.md
- `Guardrails` --implements--> `RAG anti-hallucination guardrails`  [INFERRED]
  src/agent/guardrails.py → docs/TECHNICAL_DOCUMENTATION.md
- `WhatsAppOrchestrator` --implements--> `Conversation state with TTL`  [INFERRED]
  src/agent/orchestrator.py → docs/TECHNICAL_DOCUMENTATION.md
- `WhatsAppOrchestrator` --implements--> `RAG anti-hallucination guardrails`  [INFERRED]
  src/agent/orchestrator.py → docs/TECHNICAL_DOCUMENTATION.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Webhook ingest flow (signature, dedup, rate limit, enqueue)** — src_main_create_app, src_state_base_deduplicator, src_state_base_ratelimiter, src_dispatch_local_localdispatcher, src_pipeline_messageprocessor [EXTRACTED 1.00]
- **Resilience controls** — src_utils_resilience_circuitbreaker, src_utils_resilience_backoff_delay, src_pipeline_messageprocessor, src_dispatch_redis_dispatcher_redisdispatcher [INFERRED 0.85]
- **Pluggable state backends (memory | redis)** — src_state_memory_memorystatestore, src_state_redis_state_redisstatestore, src_state_redis_state_redisdeduplicator, src_state_redis_state_redisratelimiter, src_state_init_build_state [EXTRACTED 1.00]

## Communities (44 total, 16 thin omitted)

### Community 0 - "Arranque y registro de conversaciones"
Cohesion: 0.05
Nodes (49): Conversation registry guide, Single batched registry writer, Google Sheets RAW writes, os, Settings, build_components(), create_backend(), GoogleSheetsBackend (+41 more)

### Community 1 - "Árbol de conversación"
Cohesion: 0.05
Nodes (29): dict, re, ReplyAction, Stateful conversation-tree engine. Runs the markdown flows from ``tree.md``:…, Return a label, 'next', or 'reask'., Match a whole word/token, so 'a' does not match 'banana'., Execute steps from ``start_index`` until a question (await) or the end. Returns…, Return a ReplyAction if the tree handled the message, else None. (+21 more)

### Community 2 - "RAG e ingesta de conocimiento"
Cohesion: 0.05
Nodes (51): dataclasses, Add knowledge guide, RAG anti-hallucination guardrails, json, Sample knowledge base (ACME demo), Demo ACME knowledge base (pending), lancedb, Namespace (+43 more)

### Community 3 - "Scripts CLI y prueba de carga"
Cohesion: 0.05
Nodes (35): argparse, csv, fastapi_testclient, hashlib, hmac, httpx, math, Row (+27 more)

### Community 4 - "Fixtures de pruebas y errores de config"
Cohesion: 0.09
Nodes (34): Exception, pytest, shutil, socket, ConfigError, RuntimeError, Raised at startup when a production deployment is misconfigured., AppComponents (+26 more)

### Community 5 - "Configuración y despliegue"
Cohesion: 0.09
Nodes (39): CI workflow, Main configuration (config.yaml), Placeholder website/advisor number (pending), Deployment / reverse proxy guide, Compose base stack (chatbot + ollama), Compose Caddy overlay, Compose Redis overlay, Compose Cloudflare Tunnel overlay (+31 more)

### Community 6 - "Orquestador de conversación"
Cohesion: 0.12
Nodes (20): Conversation, Process one message. Callers must not run two calls for the same phone…, WhatsAppOrchestrator, asyncio_run(), FakeEmbedder, FakeQwen, fixture, End-to-end orchestrator tests using fake embedder/Qwen and a real LanceDB table. (+12 more)

### Community 7 - "Guardrails anti-alucinación"
Cohesion: 0.12
Nodes (18): Guardrails, Guardrails: enforce the chatbot's safety restrictions. Implemented checks: *…, Return the refusal message if the message targets the bot's internals., The best retrieved chunk must clear the similarity threshold., _tokenize(), Orchestrator: full RAG pipeline for an incoming WhatsApp message. Flow ---- 1.…, build_system_prompt(), Prompt templates. The system prompt enforces the three core restrictions: 1.… (+10 more)

### Community 8 - "Cola local (LocalDispatcher)"
Cohesion: 0.16
Nodes (15): InboundMessage, LocalDispatcher, In-process queue: per-phone order, bounded parallelism, backpressure, drain., run(), test_backpressure_rejects_when_full(), scenario(), test_fairness_chatty_phone_does_not_starve_others(), scenario() (+7 more)

### Community 9 - "Contrato de la cola (buzón por teléfono)"
Cohesion: 0.11
Nodes (15): asyncio, Mailbox per phone queue, Dispatcher, ABC, Handler, Queue contracts. Design: *mailbox per phone*. Every phone number has its own…, Stop accepting, drain within the configured budget, stop workers., Backlog (queued + in flight). Redis backend: last observed value. (+7 more)

### Community 10 - "App FastAPI y privacidad de datos"
Cohesion: 0.13
Nodes (20): contextlib, Ley 1581 de 2012 - habeas data, fastapi_responses, check_settings(), create_app(), _ingest(), _notify(), webhook_receive() (+12 more)

### Community 11 - "Modelos de configuración"
Cohesion: 0.13
Nodes (23): BaseModel, dotenv, pydantic, AgentConfig, AppConfig, _apply_env_overrides(), ConversationLogConfig, _env_num() (+15 more)

### Community 12 - "Cliente Meta y reintentos"
Cohesion: 0.11
Nodes (11): Meta send retries (429/5xx), Response, backoff_delay(), Exponential backoff with full jitter (attempt starts at 0)., MetaWhatsAppClient, MetaWhatsAppError, RuntimeError, POST to the Graph API, retrying 429 / 5xx / network errors with backoff. Other… (+3 more)

### Community 13 - "Embeddings"
Cohesion: 0.14
Nodes (11): EmbeddingsConfig, build_embedder(), EmbeddingProvider, LocalEmbedder, _run(), OpenAICompatEmbedder, Protocol, Embedding providers. Two pluggable backends: * ``openai_compat`` - any OpenAI-… (+3 more)

### Community 14 - "Cliente LLM Qwen"
Cohesion: 0.12
Nodes (9): openai, LLMConfig, QwenClient, Qwen LLM client over any OpenAI-compatible endpoint. Works with vLLM, Ollama,…, Single completion call. ``messages`` are prior turns (without system)., Readiness probe: the OpenAI-compatible endpoint answers /models., ready(), CircuitBreaker (+1 more)

### Community 15 - "Validación de configuración"
Cohesion: 0.14
Nodes (15): _iter_strings(), Return a list of human-readable configuration problems (empty = OK)., validate_settings(), Clock, _meta_client(), Circuit breaker, Meta retries, memory state, PII masking, config validation,…, Tree session lives in the state store between messages, not in the process., test_circuit_breaker_opens_half_opens_and_closes() (+7 more)

### Community 16 - "Contratos de estado (dedup, rate limit)"
Cohesion: 0.16
Nodes (13): Deduplicator, Any, Protocol, RateLimiter, State contracts shared by the memory and Redis backends., True the first time ``key`` is seen within the TTL, False afterwards., Register one message and return how many were seen in the current window., Per-phone conversation snapshot (history + tree session) with idle TTL. (+5 more)

### Community 17 - "Pruebas con Redis real"
Cohesion: 0.18
Nodes (16): fixture, REDIS_URL from the environment (CI service) or a throwaway redis-server., redis_url(), _client(), Redis queue + state against a real Redis server (skipped when unavailable)., run(), test_already_processed_message_is_not_replayed(), scenario() (+8 more)

### Community 18 - "Estado en Redis y rate limit"
Cohesion: 0.17
Nodes (8): Per-phone rate limiting, Any, Redis state backend (shared across replicas, survives restarts)., RedisDeduplicator, RedisRateLimiter, RedisStateStore, test_redis_state_dedup_and_rate_limit(), scenario()

### Community 19 - "Carga de settings y OAuth Sheets"
Cohesion: 0.15
Nodes (16): main(), One-time Google OAuth2 login for the conversation registry (google_sheets).…, _deep_merge(), load_settings(), _load_yaml(), Path, Load config.yaml, overlay .env, return validated Settings., sys (+8 more)

### Community 20 - "Estado en memoria"
Cohesion: 0.15
Nodes (4): MemoryStateStore, Any, OrderedDict-based LRU with per-entry expiry., TTLCache

### Community 22 - "Pipeline de procesamiento"
Cohesion: 0.18
Nodes (10): logging_handlers, random, Worker-side processing of one queued message (runs inside the dispatcher).…, Logging setup. Writes every application log both to the console and to a…, CircuitOpenError, RuntimeError, Resilience primitives: circuit breaker and retry backoff., The downstream dependency is failing; the call was not attempted. (+2 more)

### Community 23 - "Logging rotado"
Cohesion: 0.20
Nodes (13): _ensure_file_handler(), _file_handler(), _log_file_path(), _purge_old_logs(), Handler, Path, setup_logging(), isolated_logger() (+5 more)

### Community 24 - "Dedup y límite en memoria"
Cohesion: 0.21
Nodes (8): collections, copy, MemoryDeduplicator, MemoryRateLimiter, In-process state backend: bounded (LRU) and expiring (TTL). Safe inside one…, Fixed-window counter per phone., test_memory_state_dedup_and_rate_limit(), scenario()

### Community 25 - "Servicio de registro"
Cohesion: 0.24
Nodes (5): ConversationRegistry, Logs conversation turns, enriched with the contact directory. Two modes: *…, Flush buffered records and stop the writer., test_registry_background_writer_batches_everything(), scenario()

### Community 26 - "Monitoreo Prometheus/Grafana"
Cohesion: 0.36
Nodes (7): Prometheus alert rules, Prometheus scrape config, Compose monitoring overlay (Prometheus + Grafana), Alert rules, Prometheus metrics (metabot_*), prometheus_client, Prometheus metrics (exposed on GET /metrics, scraped by Prometheus). Metric…

### Community 27 - "Paquete registry"
Cohesion: 0.33
Nodes (4): datetime, Conversation registry: who talked to the bot, when, and what was said. Every…, High-level registry service: builds records, enriches and persists them., zoneinfo

### Community 29 - "Simulador de chat CLI"
Cohesion: 0.60
Nodes (4): main(), Interactive chat simulator for the WhatsApp chatbot. Feeds messages through the…, render(), run()

## Knowledge Gaps
- **9 isolated node(s):** `chatbot-meta-qwen`, `entrypoint.sh script`, `Deployment / reverse proxy guide`, `Compose Caddy overlay`, `Compose Cloudflare Tunnel overlay` (+4 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 287 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **16 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Technical documentation` connect `Configuración y despliegue` to `Arranque y registro de conversaciones`, `Árbol de conversación`, `RAG e ingesta de conocimiento`, `Fixtures de pruebas y errores de config`, `Orquestador de conversación`, `Guardrails anti-alucinación`, `Cola local (LocalDispatcher)`, `Contrato de la cola (buzón por teléfono)`, `App FastAPI y privacidad de datos`, `Cliente Meta y reintentos`, `Cliente LLM Qwen`, `Validación de configuración`, `Contratos de estado (dedup, rate limit)`, `Estado en Redis y rate limit`, `Carga de settings y OAuth Sheets`, `Cola Redis (RedisDispatcher)`, `Servicio de registro`, `Monitoreo Prometheus/Grafana`, `MessageProcessor`?**
  _High betweenness centrality (0.126) - this node is a cross-community bridge._
- **Why does `TreeEngine` connect `Árbol de conversación` to `Configuración y despliegue`, `Orquestador de conversación`, `Guardrails anti-alucinación`?**
  _High betweenness centrality (0.082) - this node is a cross-community bridge._
- **Why does `Settings` connect `Arranque y registro de conversaciones` to `Fixtures de pruebas y errores de config`, `Configuración y despliegue`, `Guardrails anti-alucinación`, `Contrato de la cola (buzón por teléfono)`, `App FastAPI y privacidad de datos`, `Modelos de configuración`, `Validación de configuración`, `Contratos de estado (dedup, rate limit)`, `Carga de settings y OAuth Sheets`, `Pipeline de procesamiento`, `Paquete registry`?**
  _High betweenness centrality (0.066) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `Settings` (e.g. with `Main configuration (config.yaml)` and `build_system_prompt()`) actually correct?**
  _`Settings` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `RedisDispatcher` (e.g. with `Compose Redis overlay` and `Backpressure (queue.max_pending)`) actually correct?**
  _`RedisDispatcher` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `InboundMessage` (e.g. with `LocalDispatcher` and `RedisDispatcher`) actually correct?**
  _`InboundMessage` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `WhatsAppOrchestrator` (e.g. with `Conversation state with TTL` and `RAG anti-hallucination guardrails`) actually correct?**
  _`WhatsAppOrchestrator` has 5 INFERRED edges - model-reasoned connections that need verification._