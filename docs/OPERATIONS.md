# Operación en producción — Meta Bot

Guía de arquitectura y operación para desplegar el bot en varios escenarios
(una instancia, varias réplicas, varios bots/tenants) con control de colas,
seguridad y métricas externas.

---

## 1. Flujo de un mensaje

```
Meta ──HTTPS──► Cloudflare Tunnel / Caddy ──► POST /webhook  (respuesta 200 en < 50 ms)
                  (solo /webhook y /health)      │
                                                 ├─ 1. Firma X-Hub-Signature-256 (fail-closed en producción)
                                                 ├─ 2. Deduplicación por msg_id (Meta reentrega hasta ~24 h)
                                                 ├─ 3. Rate limit por teléfono + truncado de texto
                                                 └─ 4. dispatcher.submit()  ── cola llena → busy_message
                                                          │
                              ┌───────────────────────────┘
                              ▼
                 Cola: un buzón FIFO por teléfono + fila de "listos"
                              │   (K workers = QUEUE_WORKERS)
                              ▼
     Worker ─► Orquestador (árbol → guardrails → RAG → Qwen) ─► Meta Send API ─► Registro (lotes)
               │  estado de la conversación: carga → procesa → guarda (TTL)
               └─ timeout por mensaje / circuit breaker del LLM → fallback al cliente
```

**Garantías de la cola**

| Propiedad | Cómo se logra |
|---|---|
| Orden por conversación | Un buzón FIFO por teléfono; un teléfono solo lo atiende un worker a la vez. |
| Paralelismo acotado | `QUEUE_WORKERS` conversaciones simultáneas por instancia (igual a `OLLAMA_NUM_PARALLEL`). |
| Equidad | Tras cada mensaje el teléfono vuelve al final de la fila: un usuario insistente no bloquea a otros. |
| Backpressure | `queue.max_pending` (en cola + en proceso). Por encima → `busy_message`, nunca cola infinita. |
| Idempotencia | `msg_id` deduplicado al ingresar; en Redis además marca `done` tras procesar. |
| Apagado ordenado | SIGTERM: deja de aceptar, drena hasta `drain_timeout_seconds` (compose da 40 s). |
| Sin pérdida (Redis) | Backlog en Redis (AOF). Si una réplica muere, otra re-encola su trabajo tras `orphan_after_seconds`. Entrega *at-least-once*. |

---

## 2. Modos de despliegue

| Escenario | Comando | Cola / estado |
|---|---|---|
| Local / piloto (1 instancia) | `docker compose up -d` | `memory` (sin infraestructura extra) |
| Producción con HTTPS | `… -f docker-compose.caddy.yml [-f docker-compose.tunnel.yml] up -d` | `memory` |
| Producción sin pérdida de mensajes / N réplicas | `… -f docker-compose.redis.yml up -d` | `redis` |
| Con monitoreo | `… -f docker-compose.monitoring.yml up -d` | cualquiera |

Ejemplo completo (Redis + HTTPS por túnel + monitoreo):

```bash
docker compose -f docker-compose.yml -f docker-compose.redis.yml \
  -f docker-compose.caddy.yml -f docker-compose.tunnel.yml \
  -f docker-compose.monitoring.yml up -d
```

**Varias réplicas**: quitar `container_name` del servicio `chatbot` y usar
`--scale chatbot=N` (o réplicas en Portainer/Swarm). Caddy balancea por DNS
del servicio (`chatbot:8000`) y Prometheus descubre todas las réplicas.

**Varios bots / tenants**: un despliegue por bot con su propio `.env`,
`config/config.<APP_ENV>.yaml`, `tree.md` y `knowledge_base/`. Si comparten un
Redis, usar un `REDIS_PREFIX` distinto por bot.

---

## 3. Dimensionamiento

Regla principal: **`QUEUE_WORKERS` × réplicas ≤ `OLLAMA_NUM_PARALLEL`**
(o la concurrencia real de vLLM). Más workers que capacidad del LLM solo
genera timeouts.

Throughput aproximado = `OLLAMA_NUM_PARALLEL / latencia_media_LLM`.

| Hardware (estimación orientativa — medir con load_test) | Modelo | `OLLAMA_NUM_PARALLEL` | Latencia típica | Throughput |
|---|---|---|---|---|
| CPU 8 núcleos, 16 GB | qwen3.5:4b | 2 | 5–15 s | ~10–20 msg/min |
| GPU 24 GB | qwen3.5:4b | 4–8 | 1–3 s | ~100+ msg/min |
| GPU + vLLM (`qwen-service/`) | Qwen3.5-35B-A3B | batching continuo | 1–4 s | según GPU |

Prueba de carga de referencia (entorno de pruebas, LLM simulado 1.5 s, 2 en
paralelo): 200 mensajes de 50 clientes simultáneos, 0 errores, webhook p95
≈ 200 ms, LLM nunca por encima de 2 en paralelo, tiempo total = óptimo
teórico (200 × 1.5 s / 2). Con Redis y 2 réplicas, matando una a mitad de la
carga (`kill -9`), los 40 clientes recibieron todas sus respuestas.

Mida su propio hardware con `scripts/load_test.py` (ver README → Tests).

---

## 4. Variables clave

| Variable | Default | Uso |
|---|---|---|
| `APP_ENV` | `development` | `production` = fail-closed + carga `config/config.production.yaml`, oculta `/docs` |
| `QUEUE_BACKEND` / `STATE_BACKEND` | `memory` | `redis` para durabilidad y réplicas |
| `REDIS_URL` / `REDIS_PREFIX` | — / `metabot` | conexión y namespace |
| `QUEUE_WORKERS` | 2 | conversaciones en paralelo por instancia |
| `QUEUE_MAX_PENDING` | 500 | límite de backlog |
| `QUEUE_PROCESSING_TIMEOUT_SECONDS` | 120 | presupuesto por mensaje → fallback |
| `OLLAMA_NUM_PARALLEL` / `OLLAMA_MAX_QUEUE` | 2 / 64 | capacidad del LLM |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | 60 / 2 | por llamada al LLM |
| `RATE_LIMIT_MAX_MESSAGES` / `RATE_LIMIT_WINDOW_SECONDS` | 20 / 60 | anti-abuso por teléfono |
| `LOG_RETENTION_DAYS` / `LOG_MESSAGE_CONTENT` | 30 / 0 | habeas data |

Todos los textos que ve el cliente (fallback, ocupado, en cola, límite,
solo-texto, menú del árbol) están en `config/config.yaml` → `agent` y `tree`.

---

## 5. Seguridad

- **Fail-closed**: con `APP_ENV=production` el bot no arranca si falta
  `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`
  o `WHATSAPP_APP_SECRET`, ni si `config`/`tree.md` conservan placeholders
  (`example.com`, `15551234567`, `acme`).
- **Superficie expuesta**: 8000 (bot), 8001 (Ollama), 8080 (Caddy local),
  9090/3000 (monitoreo) ligados a `127.0.0.1`. Redis sin puertos. Caddy solo
  publica `/webhook` y `/health`.
- **Contenedor no-root** (UID 10001). En Linux: `sudo chown -R 10001:10001 data logs`
  o `APP_UID=$(id -u) APP_GID=$(id -g)` en `.env`.
- **Imágenes fijadas** (`OLLAMA_VERSION`, `CADDY_VERSION`, …): actualizar de
  forma deliberada, probar y luego subir la versión.
- **Datos personales (Ley 1581 de 2012)**: teléfonos enmascarados en logs,
  textos no se registran salvo `LOG_MESSAGE_CONTENT=1` (solo depuración),
  rotación por tamaño y purga por antigüedad. El registro de conversaciones
  (SQLite/Sheets) sí guarda datos del cliente por diseño: definir responsable,
  finalidad y retención.
- **Google Sheets** escribe en modo `RAW` (evita inyección de fórmulas desde
  mensajes de clientes).

### 5.1 Pendiente manual: clave expuesta en GitHub (S-01)

La clave privada de Ollama (`ollama-models/id_ed25519`) estuvo publicada en el
repositorio público. Ya se eliminó del índice y del disco, y `ollama-models/`
está en `.gitignore`. Falta (requiere credenciales del dueño del repo):

1. **Rotar**: al reiniciar el contenedor `ollama` se genera una clave nueva.
   Si esa clave estaba asociada a una cuenta de ollama.com, eliminarla allí.
2. **Purgar el historial** (reescribe commits; coordinar con quien tenga clones):
   ```bash
   pip install git-filter-repo
   git filter-repo --path ollama-models/ --invert-paths
   git remote add origin https://github.com/davidtorresdata/chatbot_meta_qwen.git
   git push --force --all && git push --force --tags
   ```
3. **Hacer privado el repositorio** en GitHub → Settings → Danger Zone.
4. Cualquier clon o fork existente conserva la clave: por eso la rotación (1)
   es lo que realmente mitiga.

---

## 6. Observabilidad

| Endpoint | Uso |
|---|---|
| `GET /health` | Liveness (Docker healthcheck). |
| `GET /ready` | Readiness: cola activa, base de conocimiento cargada, LLM responde, circuito cerrado, Redis. `503` si algo falla. |
| `GET /metrics` | Prometheus. Solo red interna. |

Métricas principales: `metabot_queue_pending`, `metabot_queue_inflight`,
`metabot_messages_received_total{kind}`, `metabot_messages_duplicate_total`,
`metabot_messages_rejected_total{reason}`,
`metabot_messages_processed_total{action,outcome}`,
`metabot_processing_seconds`, `metabot_queue_wait_seconds`,
`metabot_llm_seconds`, `metabot_llm_errors_total`,
`metabot_circuit_open{name}`, `metabot_meta_send_errors_total{status}`,
`metabot_registry_dropped_total`.

Alertas incluidas (`deploy/monitoring/alerts.yml`): instancia caída, backlog
alto, cola llena, circuito LLM abierto, p95 > 60 s, tasa de fallback > 50 %,
errores de envío a Meta. Dashboard: Grafana → carpeta *Meta Bot*.

---

## 7. Runbook

| Síntoma | Causa probable | Acción |
|---|---|---|
| Clientes reciben "Estamos atendiendo muchas solicitudes" | Cola llena | Ver `metabot_queue_pending`; subir capacidad del LLM y `QUEUE_WORKERS`, o `QUEUE_MAX_PENDING` si la latencia es aceptable. |
| Todas las respuestas son fallback | Circuito LLM abierto / Ollama caído | `curl localhost:8000/ready`; `docker compose logs ollama`. Se recupera solo tras `circuit_cooldown_seconds`. |
| El bot no arranca en producción | Fail-closed | El log lista cada problema (`Refusing to start in production`). |
| `PermissionError` en `/app/data` o `/app/logs` | Contenedor no-root | `sudo chown -R 10001:10001 data logs`. |
| Errores 401 de Meta | Token vencido | Renovar `WHATSAPP_ACCESS_TOKEN` (usar token permanente de System User). |
| Mensajes duplicados al cliente | Reinicio a mitad de un envío (at-least-once) | Esperado y raro; revisar `metabot_messages_duplicate_total`. |

---

## 8. Limitaciones conocidas

- Backend `memory`: un crash duro (no SIGTERM) pierde el backlog en memoria.
  Usar `redis` cuando eso no sea aceptable.
- Si la cola está llena, el mensaje se descarta (el cliente recibe
  `busy_message`) y no se reintenta: Meta ya recibió `200`.
- `uv.lock` no está versionado aún: generarlo con `uv lock` en una máquina con
  acceso a PyPI y commitearlo.
- La base de conocimiento de ejemplo (`knowledge_base/`) y varias respuestas de
  `tree.md` siguen siendo contenido demo (ACME / números de prueba).
