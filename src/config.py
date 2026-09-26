"""Central configuration loader.

Settings come from ``config/config.yaml`` (human-editable tuning) and are
overridden by environment variables (.env) for secrets and endpoints.

Editing tips
------------
* Conversation temperature  -> ``llm.temperature`` in the yaml or ``TEMPERATURE`` env var.
* Redirects to sites / other WhatsApp vendors -> ``redirects`` in the yaml.
* Anti-hallucination thresholds -> ``knowledge`` in the yaml.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class AppConfig(BaseModel):
    name: str = "Fertarac"
    company: str = "Fertrac"
    language: str = "es"


class AgentConfig(BaseModel):
    history_size: int = 6
    welcome_message: str = ""
    fallback_message: str = ""
    refusal_message: str = ""
    # Operational notices (all customer-facing text lives in config, not code)
    busy_message: str = "Estamos atendiendo muchas solicitudes. Por favor intenta de nuevo en unos minutos."
    queued_message: str = "Recibimos tu mensaje, en un momento te respondemos."
    rate_limited_message: str = "Recibimos muchos mensajes seguidos. Espera un momento antes de escribir de nuevo."
    unsupported_message: str = "Por ahora solo puedo leer mensajes de texto. Escribe tu consulta, por favor."


class KnowledgeConfig(BaseModel):
    top_k: int = Field(default=5, ge=1, le=50)
    score_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    enable_grounding_check: bool = True
    grounding_min_overlap: float = Field(default=0.30, ge=0.0, le=1.0)


class LLMConfig(BaseModel):
    base_url: str = "http://localhost:8001/v1"
    api_key: str = "EMPTY"
    model: str = "Qwen/Qwen2.5-7B-Instruct"
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, gt=0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    timeout_seconds: float = Field(default=60, gt=0)
    max_retries: int = Field(default=2, ge=0, le=10)  # SDK retries with exponential backoff
    circuit_failure_threshold: int = Field(default=5, ge=1)  # consecutive failures -> open
    circuit_cooldown_seconds: float = Field(default=30, gt=0)


class EmbeddingsConfig(BaseModel):
    backend: str = "openai_compat"  # openai_compat | local
    base_url: str = ""
    api_key: str = "EMPTY"
    model: str = "BAAI/bge-small-en-v1.5"


class StorageConfig(BaseModel):
    lancedb_path: str = "data/lancedb"
    table_name: str = "knowledge_base"
    chunk_size: int = 600
    chunk_overlap: int = 100


class WhatsAppConfig(BaseModel):
    access_token: str = ""
    phone_number_id: str = ""
    verify_token: str = ""
    app_secret: str = ""
    api_version: str = "v21.0"
    graph_base_url: str = "https://graph.facebook.com"
    timeout_seconds: float = Field(default=15, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)  # on 429 / 5xx / network errors


class RedirectRule(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    message: str = ""
    url: str | None = None
    whatsapp: str | None = None


class RedirectConfig(BaseModel):
    enabled: bool = True
    rules: list[RedirectRule] = Field(default_factory=list)
    default_url: str | None = None
    default_whatsapp: str | None = None


class TreeConfig(BaseModel):
    enabled: bool = True
    path: str = "tree.md"
    menu_keywords: list[str] = Field(default_factory=lambda: ["menu", "start", "help"])
    redirect_message: str = "Continue with a human agent here:"
    max_steps: int = Field(default=30, ge=1)
    menu_header: str = "I can help you with one of these options:"
    menu_footer: str = "Reply with a number or a keyword."
    options_footer: str = "Reply with a number or an option."


class RuntimeConfig(BaseModel):
    """Deployment profile. ``production`` turns config problems into startup errors."""

    environment: str = "development"  # development | staging | production
    redis_url: str = ""  # required when queue/state backend is "redis"
    redis_prefix: str = "metabot"  # namespace; use one per tenant/bot on a shared Redis


class QueueConfig(BaseModel):
    """Inbound message queue (see docs/OPERATIONS.md)."""

    backend: str = "memory"  # memory (single instance) | redis (persistent, multi-replica)
    workers: int = Field(default=2, ge=1, le=256)  # keep == OLLAMA_NUM_PARALLEL
    max_pending: int = Field(default=500, ge=1)  # backpressure limit (queued + in flight)
    processing_timeout_seconds: float = Field(default=120, gt=0)  # per message, end to end
    drain_timeout_seconds: float = Field(default=25, gt=0)  # graceful shutdown budget
    ack_when_pending_over: int = Field(default=10, ge=0)  # 0 = never send "queued" notice
    heartbeat_seconds: float = Field(default=5, gt=0)  # redis backend
    orphan_after_seconds: float = Field(default=60, gt=0)  # redis: requeue dead worker's job


class StateConfig(BaseModel):
    """Conversation state (history + tree session), dedup and rate limit storage."""

    backend: str = "memory"  # memory | redis
    conversation_ttl_seconds: int = Field(default=1800, ge=60)  # idle conversation expiry
    max_conversations: int = Field(default=10_000, ge=100)  # memory backend LRU cap
    dedup_ttl_seconds: int = Field(default=86_400, ge=60)  # Meta may redeliver for ~24h


class RateLimitConfig(BaseModel):
    enabled: bool = True
    max_messages: int = Field(default=20, ge=1)  # per phone per window
    window_seconds: int = Field(default=60, ge=1)
    max_input_chars: int = Field(default=1000, ge=50)  # longer inputs are truncated


class ConversationLogConfig(BaseModel):
    """Conversation registry: who contacted the bot and what was said.

    Backends: none | sqlite | google_sheets. See docs/CONVERSATION_REGISTRY.md.
    """

    enabled: bool = False
    backend: str = "none"  # none | sqlite | google_sheets
    columns: list[str] = Field(default_factory=lambda: [
        "hora", "numero", "nombre", "ciudad", "empresa", "pregunta", "respuesta"
    ])
    db_path: str = "data/conversation_log.sqlite3"
    contacts_file: str = ""
    spreadsheet_id: str = ""
    worksheet: str = "Sheet1"
    google_auth_mode: str = "service_account"  # service_account | oauth_user
    google_credentials_file: str = ""  # service_account: SA JSON key
    google_client_secret_file: str = ""  # oauth_user: OAuth 2.0 client secret
    google_token_file: str = "data/gspread_authorized_user.json"  # oauth_user
    timezone: str = ""  # e.g. "America/Bogota"; empty = UTC


class Settings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    redirects: RedirectConfig = Field(default_factory=RedirectConfig)
    tree: TreeConfig = Field(default_factory=TreeConfig)
    conversation_log: ConversationLogConfig = Field(default_factory=ConversationLogConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)

    @property
    def is_production(self) -> bool:
        return self.runtime.environment.strip().lower() in ("prod", "production")


def _load_yaml(path: Path) -> dict:
    if path.is_file():
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else {}
    return {}


def _apply_env_overrides(settings: Settings) -> Settings:
    """Overlay env vars onto the settings (secrets, endpoints, temperature)."""
    if os.getenv("WHATSAPP_ACCESS_TOKEN"):
        settings.whatsapp.access_token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    if os.getenv("WHATSAPP_PHONE_NUMBER_ID"):
        settings.whatsapp.phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    if os.getenv("WHATSAPP_VERIFY_TOKEN"):
        settings.whatsapp.verify_token = os.environ["WHATSAPP_VERIFY_TOKEN"]
    if os.getenv("WHATSAPP_APP_SECRET"):
        settings.whatsapp.app_secret = os.environ["WHATSAPP_APP_SECRET"]

    if os.getenv("LLM_BASE_URL"):
        settings.llm.base_url = os.environ["LLM_BASE_URL"]
    if os.getenv("LLM_API_KEY"):
        settings.llm.api_key = os.environ["LLM_API_KEY"]
    if os.getenv("LLM_MODEL"):
        settings.llm.model = os.environ["LLM_MODEL"]
    if os.getenv("TEMPERATURE"):
        settings.llm.temperature = float(os.environ["TEMPERATURE"])

    if os.getenv("EMBEDDING_BACKEND"):
        settings.embeddings.backend = os.environ["EMBEDDING_BACKEND"]
    if os.getenv("EMBEDDING_BASE_URL"):
        settings.embeddings.base_url = os.environ["EMBEDDING_BASE_URL"]
    if os.getenv("EMBEDDING_API_KEY"):
        settings.embeddings.api_key = os.environ["EMBEDDING_API_KEY"]
    if os.getenv("EMBEDDING_MODEL"):
        settings.embeddings.model = os.environ["EMBEDDING_MODEL"]

    if os.getenv("CONVERSATION_LOG_ENABLED"):
        settings.conversation_log.enabled = os.environ["CONVERSATION_LOG_ENABLED"].strip().lower() in (
            "1", "true", "yes", "on"
        )
    if os.getenv("CONVERSATION_LOG_BACKEND"):
        settings.conversation_log.backend = os.environ["CONVERSATION_LOG_BACKEND"]
    if os.getenv("CONVERSATION_LOG_COLUMNS"):
        settings.conversation_log.columns = [
            c.strip() for c in os.environ["CONVERSATION_LOG_COLUMNS"].split(",") if c.strip()
        ]
    if os.getenv("CONVERSATION_LOG_DB_PATH"):
        settings.conversation_log.db_path = os.environ["CONVERSATION_LOG_DB_PATH"]
    if os.getenv("CONVERSATION_LOG_CONTACTS_FILE"):
        settings.conversation_log.contacts_file = os.environ["CONVERSATION_LOG_CONTACTS_FILE"]
    if os.getenv("CONVERSATION_LOG_SPREADSHEET_ID"):
        settings.conversation_log.spreadsheet_id = os.environ["CONVERSATION_LOG_SPREADSHEET_ID"]
    if os.getenv("CONVERSATION_LOG_WORKSHEET"):
        settings.conversation_log.worksheet = os.environ["CONVERSATION_LOG_WORKSHEET"]
    if os.getenv("CONVERSATION_LOG_GOOGLE_CREDENTIALS_FILE"):
        settings.conversation_log.google_credentials_file = os.environ[
            "CONVERSATION_LOG_GOOGLE_CREDENTIALS_FILE"
        ]
    if os.getenv("CONVERSATION_LOG_GOOGLE_AUTH_MODE"):
        settings.conversation_log.google_auth_mode = os.environ["CONVERSATION_LOG_GOOGLE_AUTH_MODE"]
    if os.getenv("CONVERSATION_LOG_GOOGLE_CLIENT_SECRET_FILE"):
        settings.conversation_log.google_client_secret_file = os.environ[
            "CONVERSATION_LOG_GOOGLE_CLIENT_SECRET_FILE"
        ]
    if os.getenv("CONVERSATION_LOG_GOOGLE_TOKEN_FILE"):
        settings.conversation_log.google_token_file = os.environ["CONVERSATION_LOG_GOOGLE_TOKEN_FILE"]
    if os.getenv("CONVERSATION_LOG_TIMEZONE"):
        settings.conversation_log.timezone = os.environ["CONVERSATION_LOG_TIMEZONE"]

    # --- runtime / scaling -------------------------------------------------
    _env_str(settings.runtime, "environment", "APP_ENV")
    _env_str(settings.runtime, "redis_url", "REDIS_URL")
    _env_str(settings.runtime, "redis_prefix", "REDIS_PREFIX")
    _env_str(settings.queue, "backend", "QUEUE_BACKEND")
    _env_num(settings.queue, "workers", "QUEUE_WORKERS", int)
    _env_num(settings.queue, "max_pending", "QUEUE_MAX_PENDING", int)
    _env_num(settings.queue, "processing_timeout_seconds", "QUEUE_PROCESSING_TIMEOUT_SECONDS", float)
    _env_str(settings.state, "backend", "STATE_BACKEND")
    _env_num(settings.state, "conversation_ttl_seconds", "STATE_CONVERSATION_TTL_SECONDS", int)
    _env_num(settings.rate_limit, "max_messages", "RATE_LIMIT_MAX_MESSAGES", int)
    _env_num(settings.rate_limit, "window_seconds", "RATE_LIMIT_WINDOW_SECONDS", int)
    _env_num(settings.llm, "timeout_seconds", "LLM_TIMEOUT_SECONDS", float)
    _env_num(settings.llm, "max_retries", "LLM_MAX_RETRIES", int)
    _env_str(settings.whatsapp, "graph_base_url", "WHATSAPP_GRAPH_BASE_URL")
    if os.getenv("RATE_LIMIT_ENABLED"):
        settings.rate_limit.enabled = _truthy(os.environ["RATE_LIMIT_ENABLED"])
    return settings


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_str(section: BaseModel, attr: str, env: str) -> None:
    value = os.getenv(env)
    if value:
        setattr(section, attr, value.strip())


def _env_num(section: BaseModel, attr: str, env: str, cast) -> None:
    value = os.getenv(env)
    if value:
        setattr(section, attr, cast(value))


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load config.yaml, overlay .env, return validated Settings."""
    load_dotenv(PROJECT_ROOT / ".env")

    path = Path(config_path) if config_path else None
    if path is None and os.getenv("CONFIG_PATH"):
        path = Path(os.environ["CONFIG_PATH"])
    if path is None:
        path = PROJECT_ROOT / "config" / "config.yaml"

    data = _load_yaml(path)
    # Environment profile overlay: config/config.<APP_ENV>.yaml (optional),
    # e.g. config.production.yaml with real URLs/numbers for that deployment.
    env_name = os.getenv("APP_ENV", str(data.get("runtime", {}).get("environment", ""))).strip().lower()
    if env_name:
        data = _deep_merge(data, _load_yaml(path.with_name(f"{path.stem}.{env_name}{path.suffix}")))
    settings = Settings.model_validate(data)
    return _apply_env_overrides(settings)


# ------------------------------------------------------------------ validation
PLACEHOLDER_MARKERS = ("example.com", "15551234567", "acme")
_VALID_BACKENDS = {"queue": {"memory", "redis"}, "state": {"memory", "redis"}}


class ConfigError(RuntimeError):
    """Raised at startup when a production deployment is misconfigured."""


def validate_settings(settings: Settings) -> list[str]:
    """Return a list of human-readable configuration problems (empty = OK)."""
    problems: list[str] = []
    wa = settings.whatsapp
    for attr, env in (
        ("access_token", "WHATSAPP_ACCESS_TOKEN"),
        ("phone_number_id", "WHATSAPP_PHONE_NUMBER_ID"),
        ("verify_token", "WHATSAPP_VERIFY_TOKEN"),
        ("app_secret", "WHATSAPP_APP_SECRET"),
    ):
        if not getattr(wa, attr):
            problems.append(f"{env} is not set")

    for section, allowed in _VALID_BACKENDS.items():
        backend = getattr(settings, section).backend.strip().lower()
        if backend not in allowed:
            problems.append(f"{section}.backend={backend!r} is not one of {sorted(allowed)}")
        if backend == "redis" and not settings.runtime.redis_url:
            problems.append(f"{section}.backend=redis requires REDIS_URL")

    if settings.queue.workers > settings.queue.max_pending:
        problems.append("queue.workers cannot exceed queue.max_pending")

    for path, value in _iter_strings(settings.model_dump(exclude={"whatsapp", "runtime"})):
        lowered = value.lower()
        for marker in PLACEHOLDER_MARKERS:
            if marker in lowered:
                problems.append(f"{path} still contains placeholder {marker!r}")
                break

    if settings.tree.enabled:
        tree_path = Path(settings.tree.path)
        if not tree_path.is_absolute():
            tree_path = PROJECT_ROOT / tree_path
        try:
            tree_text = tree_path.read_text(encoding="utf-8").lower()
        except OSError:
            problems.append(f"tree.path {settings.tree.path!r} cannot be read")
        else:
            for marker in PLACEHOLDER_MARKERS:
                if marker in tree_text:
                    problems.append(f"{settings.tree.path} still contains placeholder {marker!r}")
    return problems


def _iter_strings(obj, prefix: str = ""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _iter_strings(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _iter_strings(value, f"{prefix}[{index}]")
    elif isinstance(obj, str):
        yield prefix, obj
