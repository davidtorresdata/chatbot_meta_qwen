# User Manual — WhatsApp Chatbot (Qwen + LanceDB)

Practical guide for operating the WhatsApp customer-service chatbot. Written
for non-developers. For code-level details, see the
[Technical Documentation](TECHNICAL_DOCUMENTATION.md).

- [1. What this bot does](#1-what-this-bot-does)
- [2. How it works (plain English)](#2-how-it-works-plain-english)
- [3. What you need](#3-what-you-need)
- [4. Quick start (Docker)](#4-quick-start-docker)
- [5. Connect WhatsApp](#5-connect-whatsapp)
- [6. Configure the bot](#6-configure-the-bot)
- [7. Add your own knowledge](#7-add-your-own-knowledge)
- [8. Redirect rules](#8-redirect-rules)
- [9. Day-to-day operation](#9-day-to-day-operation)
- [10. Tuning the bot](#10-tuning-the-bot)
- [11. Managing the AI models](#11-managing-the-ai-models)
- [12. Troubleshooting](#12-troubleshooting)
- [13. FAQ](#13-faq)
- [14. Backup & maintenance](#14-backup--maintenance)

---

## 1. What this bot does

The bot receives WhatsApp messages and replies automatically as your company's
virtual assistant. It only answers using the documents you give it (a
"knowledge base"): product FAQ, policies, hours, contact info, etc. It does
**not** make up answers from nowhere — if your documents don't contain the
answer, the bot politely says it doesn't know and points the customer to your
website or a human agent.

It can also:

- Answer follow-up questions in the same conversation (remembers recent turns).
- Show a **"Open" button** that opens your website or starts a WhatsApp chat
  with a sales agent, when the customer asks about prices, a person, etc.
- Refuse to discuss how the bot itself was built.

## 2. How it works (plain English)

```
Customer message ──> WhatsApp ──> Bot server ──> "Understand" the question
                                                  └─> Look up best-matching knowledge
                                                  └─> AI writes answer using only that knowledge
                                                  └─> Double-check: answer matches knowledge?
                                                  └─> Reply on WhatsApp
```

Two AI "models" do the work, both managed for you by Ollama:

- **Chat model** (`qwen3.5:4b`) — writes the replies.
- **Embedding model** (`bge-m3`) — converts text into numbers so the bot can
  find which of your documents best matches a question.

The knowledge lives in a local searchable database (LanceDB) on the server.

## 3. What you need

- A **server** (or PC that stays on) with **Docker** installed and **~15 GB of
  free disk space** and at least **8 GB of RAM**. No graphics card required.
- A **Meta for Developers account** with a WhatsApp Business profile (see
  section 5).
- Basic command-line access (terminal on macOS/Linux, PowerShell on Windows).

## 4. Quick start (Docker)

The entire bot + AI runs with one command.

```bash
# 1. Copy the example config and open it in a text editor
copy .env.example .env        # Windows
cp .env.example .env          # Linux / macOS

# 2. Start everything (first run downloads ~5 GB of AI models)
docker compose up -d
```

The first start downloads the AI models. You can watch progress:

```bash
docker compose logs -f ollama
# wait until you see "success" twice (once per model), then Ctrl+C to stop watching
```

Verify the AI is ready:

```bash
curl http://localhost:8001/v1/models
# should list "qwen3.5:4b" and "bge-m3"
```

At this point the *AI* works, but the bot isn't connected to WhatsApp yet.
Continue to section 5.

## 5. Connect WhatsApp

The bot answers messages sent to a **WhatsApp Business number** through the
Meta Cloud API.

1. Go to the [Meta for Developers](https://developers.facebook.com) portal and
   create an app of type **Business**.
2. Add the **WhatsApp** product to the app.
3. Follow the setup to create/select a **WhatsApp Business Profile** and link a
   **phone number** (you can use a test number during development).
4. On the WhatsApp → **API Setup** page you'll find three values you need:
   - **Access token** (temporary during setup, or create a **permanent token**
     from the app's settings),
   - **Phone number ID**,
   - **Verify token** — *you* choose this: any secret string (e.g.
     `mysecret123`). It proves to Meta that your server is really yours.
   - **App Secret** — from App settings → Basic. It lets the bot check that
     incoming webhooks really come from Meta (see step 9).
5. Put these values into the `.env` file:

   ```
   WHATSAPP_ACCESS_TOKEN=<access token from Meta>
   WHATSAPP_PHONE_NUMBER_ID=<phone number id from Meta>
   WHATSAPP_VERIFY_TOKEN=<the secret string you chose>
   WHATSAPP_APP_SECRET=<App Secret from App settings -> Basic>
   ```

6. Restart the bot so it picks up the new settings:

   ```bash
   docker compose restart chatbot
   ```

7. Tell Meta where to send messages. In the WhatsApp → **Configuration** page:
   - **Callback URL:** `https://<your-server-domain>/webhook`
   - **Verify token:** the same secret string from step 4
   - Click **Verify and save**. If verification succeeds, the webhook shows as
     "Configured".
8. Subscribe to the message webhook fields (in the **Webhooks** section, add
   the `messages` field for your WhatsApp Business Account).
9. **Verifying payloads** — with `WHATSAPP_APP_SECRET` set, the bot rejects any
   webhook whose `X-Hub-Signature-256` header does not match
   `HMAC-SHA256(body, app_secret)`, so forged requests can never be processed.

> **Note:** Meta requires a **public HTTPS URL** for the webhook. You do **not**
> need to expose the whole app — only `/webhook`. The repo ships a Caddy
> reverse proxy that works out of the box on `http://localhost:8080`
> (`docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d`);
> for production HTTPS set `CADDY_DOMAIN` in `.env`. See `deploy/README.md`.
> Use a tunnel only during testing.

Test it: message your WhatsApp number. The bot should reply.

## 6. Configure the bot

There are two places to configure things:

### 6.1 `.env` — accounts and endpoints (secrets live here)

| Setting | What it is |
|---|---|
| `WHATSAPP_ACCESS_TOKEN` | Your Meta access token |
| `WHATSAPP_PHONE_NUMBER_ID` | Your Meta phone number ID |
| `WHATSAPP_VERIFY_TOKEN` | Your webhook verify secret |
| `LLM_BASE_URL` | AI server address (leave as `http://localhost:8001/v1`) |
| `LLM_API_KEY` | Usually `EMPTY` for a local AI |
| `LLM_MODEL` | Which chat model to use (`qwen3.5:4b`) |
| `TEMPERATURE` | 0 = same answer every time, 1 = more creative |
| `EMBEDDING_BACKEND` | `openai_compat` (default) or `local` (fully offline) |
| `EMBEDDING_MODEL` | Embedding model (`bge-m3`) |

### 6.2 `config/config.yaml` — behaviour (safe to edit)

| Setting | What it does | Tip |
|---|---|---|
| `app.language` | Language the bot replies in | `en`, `es`, etc. |
| `agent.fallback_message` | Message when the bot doesn't know the answer | Keep it short + add your site |
| `agent.refusal_message` | Message when asked about the bot's internals | |
| `knowledge.top_k` | How many knowledge pieces the bot checks per question | 3–8 works well |
| `knowledge.score_threshold` | How sure the bot must be before answering | Lower = answers more, risks wrong answers |
| `knowledge.enable_grounding_check` | Extra safety check on the answer | Keep `true` |
| `llm.max_tokens` | Maximum answer length | Raise if answers get cut off |
| `llm.temperature` | Creativity (same as `TEMPERATURE`) | 0.3 is a good default |
| `storage.chunk_size` / `chunk_overlap` | How the knowledge is split at ingestion | Only applies when you re-ingest |
| `redirects.enabled` | Master switch for redirect buttons | |

After editing `config/config.yaml`:

```bash
docker compose restart chatbot
```

## 7. Add your own knowledge

1. Put your documents in the `knowledge_base/` folder. Supported formats:
   `.txt`, `.md`, `.pdf`, `.xlsx`. For Excel: **the first row of each sheet
   must be column titles**, and every data row becomes one searchable fact.
   The complete procedure (with layout rules and examples) is in
   `docs/ADD_KNOWLEDGE.md`.
2. Tell the bot to read them:

   ```bash
   docker compose exec chatbot python scripts/ingest_cli.py knowledge_base/
   ```

   That's it. The bot can now answer using those documents.

Helpful commands:

```bash
# ingest a specific folder
docker compose exec chatbot python scripts/ingest_cli.py docs/

# ingest a single file
docker compose exec chatbot python scripts/ingest_cli.py faq.pdf

# rebuild from scratch (do this after changing the embedding model)
docker compose exec chatbot python scripts/ingest_cli.py knowledge_base/ --reset

# see how many knowledge pieces are stored
curl http://localhost:8000/health      # shows "chunks": N
```

> **Tip:** After editing a document, re-run the ingest command so the bot uses
> the new text. Every question is answered only from the current knowledge.

## 8. Redirect rules

Redirect rules send the customer to a **website button** or to **another
WhatsApp number** when their message contains certain keywords.

In `config/config.yaml`:

```yaml
redirects:
  enabled: true
  rules:
    - keywords: ["price", "cost", "precio"]
      message: "You can see our pricing here:"
      url: "https://acme.example.com/pricing"
    - keywords: ["agent", "human", "sales"]
      message: "Talk to one of our sales agents:"
      whatsapp: "15551234567"
  default_url: "https://acme.example.com"
  default_whatsapp: "15551234567"
```

- `keywords`: if any word appears in the message, the rule fires. First match
  wins.
- `url`: the customer gets a button that opens this website.
- `whatsapp`: the customer gets a button that opens a chat with that number
  (international format, digits only).
- `default_*`: used when the bot doesn't know an answer — it sends the fallback
  message plus a button to your site / a human.

## 9. Conversation tree (scripted flows)

For questions the AI does not need to answer with free text, you can script
**flows** in plain Markdown (`tree.md`): ask questions, offer menus of options,
route by keyword, and end with a button that opens a chat with a human agent.

A flow is a `## <name>` heading plus metadata and steps:

```
## returns

Menu: Returns & refunds
Keywords: return, refund
Description: Guide a customer through a return request.

- question: What is your order number? -> field=order
- branch: * -> @found
- answer @found: Thanks! We found order {order}.
- message: If you need more help, an agent is one message away:
- redirect: 15551234567
```

- The flow starts automatically when the customer's message contains one of the
  `Keywords:`.
- Typing `menu` (or `start` / `help`) lists the flows and lets the customer pick
  one by number.
- If no flow matches and no flow is active, the bot falls back to the normal AI
  answer.
- After editing `tree.md`, restart with `docker compose restart chatbot`.
- Enable/disable the whole feature with `tree.enabled` in `config/config.yaml`.

Full reference (options, branches, placeholders): `docs/CONVERSATION_TREE.md`.

## 10. Day-to-day operation

| Task | Command |
|---|---|
| Start the bot | `docker compose up -d` |
| Stop the bot | `docker compose down` (keeps knowledge & models) |
| See live chat logs | `docker compose logs -f chatbot` |
| See AI model logs | `docker compose logs -f ollama` |
| Check everything is healthy | `docker compose ps` (both should say "healthy") |
| Check knowledge count | `curl http://localhost:8000/health` |
| Check the bot is ready (AI, queue, knowledge) | `curl http://localhost:8000/ready` (must say `"ready"`) |
| See pending messages in the queue | `/ready` → `queue.pending`, or the Grafana dashboard |
| Restart after config changes | `docker compose restart chatbot` |
| Read the action log file | `Get-ChildItem logs` (host) — see below |

### 10.1 Action logs (rotated, privacy-safe)

Every action is written to a log file that survives restarts and rebuilds.
Each time the bot starts it opens a new file:

```
logs/wa_ollama_logs_<YYYYMMDD_HHMMSS_uuuuuu>.txt
```

The file records every inbound message (masked phone, message id, type),
every reply the bot computed (type, outcome), duplicates, rate-limited or
rejected messages, plus errors and full tracebacks. The same files also record
knowledge-ingest runs. Logs live on the host under `logs/` (mounted into the
container as `/app/logs`); set the `LOG_DIR` environment variable to change the
folder.

Personal data protection (Ley 1581 de 2012):

- Phone numbers appear masked (`5730*****567`).
- Message texts are **not** written (only their length) unless
  `LOG_MESSAGE_CONTENT=1` is set for local debugging — never in production.
- Files rotate at 20 MB (`LOG_MAX_BYTES`, 5 backups) and files older than
  30 days are deleted at startup (`LOG_RETENTION_DAYS`).

Logs are your main diagnostic tool — if a customer says the bot didn't reply,
the reason is usually visible in `docker compose logs chatbot`.

## 11. Tuning the bot

Common adjustments and where to make them:

| I want the bot to… | Change |
|---|---|
| Sound more/less creative | `TEMPERATURE` in `.env` (0–1) |
| Give longer answers | `llm.max_tokens` in `config.yaml` |
| Answer more confidently | Lower `knowledge.score_threshold` (e.g. 0.25) |
| Be more careful (fewer wrong answers) | Raise `knowledge.score_threshold` (e.g. 0.45) |
| Remember more conversation | Raise `agent.history_size` |
| Remember less | Lower `agent.history_size` |
| Reply in another language | `app.language` in `config.yaml` |

Each change takes effect after `docker compose restart chatbot`. If the bot
starts giving wrong answers, raise `score_threshold` or improve the knowledge
documents — never the other way around.

## 12. Managing the AI models

The default models are downloaded automatically on first start and cached in
the `ollama-models/` folder.

- **Switch chat model** (e.g. to a smarter/bigger one): set `LLM_MODEL` in
  `.env` (for example `qwen3.5:9b`), add the same name to the `ollama pull`
  list in `docker-compose.yml`, then `docker compose up -d`. Bigger models need
  more RAM.
- **Switch embedding model**: set `EMBEDDING_MODEL` and add it to the pull list
  in `docker-compose.yml`, then **rebuild the knowledge**
  (`ingest_cli.py knowledge_base/ --reset`).
- **Remove a model** (free disk space): inside the ollama container,
  `docker compose exec ollama ollama rm qwen3.5:4b`.

A rough guide for RAM needs (chat model + embeddings): `qwen3.5:4b` ≈ 8 GB,
`qwen3.5:9b` ≈ 12 GB, larger models need more.

## 13. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Bot never replies on WhatsApp | Webhook not configured or verify failed | Check section 5; confirm the Callback URL is public + HTTPS; re-verify |
| `docker compose ps` shows chatbot not "healthy" | No knowledge ingested yet or config error | Check `docker compose logs chatbot`; run the ingest command |
| Model still downloading on every restart | `ollama-models/` volume missing/removed | Don't delete `ollama-models/`; keep `./ollama-models:/root/.ollama` in compose |
| Answers are wrong / not about my company | `score_threshold` too low, or stale knowledge | Raise threshold, re-ingest after doc edits |
| Bot says "I don't have that information" often | Knowledge base too small | Add more documents and re-ingest |
| Answers get cut off | `max_tokens` too small | Raise `llm.max_tokens` |
| Out of memory / bot crashes on the AI | Model too big for the server RAM | Use a smaller model (e.g. `qwen3.5:4b`), or add RAM |
| Changes to `.env`/`config.yaml` don't apply | Container not restarted | `docker compose restart chatbot` |
| Port 8000/8001 already in use | Another service on the server | Change the left-hand port in `docker-compose.yml` |
| Customers get "Estamos atendiendo muchas solicitudes…" | Queue full (more messages than the AI can answer) | See `docs/OPERATIONS.md` → Runbook: raise AI capacity / `QUEUE_WORKERS` |
| Every answer is the fallback message | AI server down (circuit breaker open) | `curl http://localhost:8000/ready`; `docker compose logs ollama` |
| Bot does not start and the log says `Refusing to start in production` | `APP_ENV=production` with a missing secret or example values left in config/`tree.md` | Fix each item listed in the log |
| `PermissionError` on `/app/data` or `/app/logs` (Linux) | Container runs as a non-root user | `sudo chown -R 10001:10001 data logs` |
| Logs show a Meta API error | Token/ID expired or invalid | Regenerate the token in the Meta portal, update `.env`, restart |

## 14. FAQ

**Does the bot need a graphics card?**
No. It runs on CPU via Ollama. GPU hosts get better speed, but it's optional.

**Can the bot answer anything not in my documents?**
No — by design. It only answers from the knowledge base. This prevents wrong
answers about products, policies, etc.

**Where are conversations stored?**
In the server's memory only. They are not saved anywhere and are lost when the
container restarts.

**How do I know what the bot knows?**
`curl http://localhost:8000/health` shows the number of stored knowledge
pieces. The content is whatever is in `knowledge_base/` (re-ingested).

**Can I change the bot's language?**
Yes — set `app.language` in `config/config.yaml` (e.g. `es`). The prompt tells
the model to answer in that language.

**Do I need to be technical to run this?**
You need to be able to run a few commands in a terminal and edit two text
files. Everything else is automated.

**Is my data sent anywhere?**
The AI models run on your own server; WhatsApp messages flow through Meta (as
required by WhatsApp itself). Nothing is sent to third-party AI services.

## 15. Backup & maintenance

To back up the whole bot (knowledge + models):

```bash
docker compose down
# copy these folders to safe storage:
#   knowledge_base/   (your documents)
#   data/             (the searchable knowledge)
#   tree.md           (your conversation-tree flows)
#   logs/             (action logs — wa_ollama_logs_*.txt)
#   ollama-models/    (AI models — optional, they re-download)
#   .env              (secrets — keep safe and private!)
docker compose up -d
```

Routine maintenance:

- Update the chatbot image: `docker compose build chatbot && docker compose up -d`.
- Update Ollama: `docker compose pull ollama && docker compose up -d`.
- Rotate the Meta access token periodically in the Meta portal, then update
  `.env` and restart.
- Keep at least **~15 GB free disk space** (models + knowledge grow).
