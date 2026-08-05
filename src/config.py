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
    timeout_seconds: float = Field(default=90, gt=0)


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
    return settings


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load config.yaml, overlay .env, return validated Settings."""
    load_dotenv(PROJECT_ROOT / ".env")

    path = Path(config_path) if config_path else None
    if path is None and os.getenv("CONFIG_PATH"):
        path = Path(os.environ["CONFIG_PATH"])
    if path is None:
        path = PROJECT_ROOT / "config" / "config.yaml"

    data = _load_yaml(path)
    settings = Settings.model_validate(data)
    return _apply_env_overrides(settings)
