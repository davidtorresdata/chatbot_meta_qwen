"""Storage backends for the conversation registry.

Choose one with ``CONVERSATION_LOG_BACKEND``:

* ``none``           -> disabled (no-op)
* ``sqlite``         -> local SQLite file (stdlib ``sqlite3``, zero extra deps)
* ``google_sheets``  -> append a row per record to a Google Spreadsheet
                        (needs ``gspread`` + a service-account JSON key that
                        has been shared to the sheet)

Adding another backend (e.g. PostgreSQL): subclass :class:`RegistryBackend`,
implement ``log()`` / ``close()``, and register it in :func:`create_backend`.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from src.registry.models import DEFAULT_COLUMNS, ConversationRecord

if TYPE_CHECKING:
    from src.config import ConversationLogConfig

logger = logging.getLogger(__name__)

SPREADSHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


def _quoted_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class RegistryBackend(ABC):
    """Minimal persistence contract for one conversation record."""

    @abstractmethod
    def log(self, record: ConversationRecord) -> None:
        """Persist one record. May raise; callers handle failures."""

    def close(self) -> None:
        pass


class NoopBackend(RegistryBackend):
    def log(self, record: ConversationRecord) -> None:
        pass


class SqliteBackend(RegistryBackend):
    """Appends records to a ``conversations`` table in a local SQLite file.

    The table is created on first use with exactly the configured columns.
    The file lives in ``./data`` by default, which is already a mounted volume
    in Docker, so records survive container restarts.
    """

    def __init__(self, path: str | Path, columns: list[str]):
        self._path = Path(path)
        self._columns = list(columns) or list(DEFAULT_COLUMNS)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        # A fresh connection per call: records are written from worker threads,
        # and SQLite connections are tied to the thread that created them.
        conn = sqlite3.connect(str(self._path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        cols = ", ".join(f"{_quoted_ident(c)} TEXT" for c in self._columns)
        with self._connect() as conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS conversations ({cols})")

    def log(self, record: ConversationRecord) -> None:
        cols = ", ".join(_quoted_ident(c) for c in self._columns)
        placeholders = ", ".join("?" for _ in self._columns)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO conversations ({cols}) VALUES ({placeholders})",
                record.values(self._columns),
            )

    def close(self) -> None:
        pass


class GoogleSheetsBackend(RegistryBackend):
    """Appends one row per record to a spreadsheet worksheet.

    Two authentication modes (``CONVERSATION_LOG_GOOGLE_AUTH_MODE``):

    * ``service_account`` (default) — uses a service-account JSON key
      (``CONVERSATION_LOG_GOOGLE_CREDENTIALS_FILE``) with the Google Sheets API
      enabled; share the spreadsheet with the service-account e-mail as Editor.
    * ``oauth_user`` — uses *your* Google account via OAuth2: a
      ``client_secret.json`` (``CONVERSATION_LOG_GOOGLE_CLIENT_SECRET_FILE``)
      plus a one-time login that stores a refresh token in
      ``CONVERSATION_LOG_GOOGLE_TOKEN_FILE``. Run the one-time login first:
      ``docker compose exec chatbot python scripts/sheets_oauth_setup.py``.

    Other requirements:
      * ``gspread`` and ``google-auth`` (both in requirements.txt);
      * ``CONVERSATION_LOG_SPREADSHEET_ID`` (from the sheet URL) and
        ``CONVERSATION_LOG_WORKSHEET`` (the tab name, default ``Sheet1``).

    The header row is written automatically on first use. Rows are appended
    below it. The Google service is only contacted when the first record is
    written, so the app can start even when offline.
    """

    def __init__(
        self,
        *,
        auth_mode: str = "service_account",
        spreadsheet_id: str,
        worksheet: str,
        columns: list[str],
        google_credentials_file: str = "",
        google_client_secret_file: str = "",
        google_token_file: str = "",
    ):
        self._columns = list(columns) or list(DEFAULT_COLUMNS)
        self._worksheet_name = worksheet
        self._client = None
        self._worksheet = None
        self._header_ready = False
        self._spreadsheet_id = spreadsheet_id

        # 1. validate inputs (no third-party imports needed)
        if auth_mode == "oauth_user":
            if not google_client_secret_file:
                raise ValueError(
                    "oauth_user mode requires CONVERSATION_LOG_GOOGLE_CLIENT_SECRET_FILE"
                )
            if not google_token_file:
                raise ValueError(
                    "oauth_user mode requires CONVERSATION_LOG_GOOGLE_TOKEN_FILE"
                )
            if not os.path.exists(google_token_file):
                raise RuntimeError(
                    f"OAuth token file not found: {google_token_file}. "
                    "Run the one-time login first: "
                    "docker compose exec chatbot python scripts/sheets_oauth_setup.py"
                )
        elif not google_credentials_file:
            raise ValueError(
                "service_account mode requires CONVERSATION_LOG_GOOGLE_CREDENTIALS_FILE"
            )

        # 2. third-party deps
        try:
            import gspread
        except ImportError as exc:
            raise RuntimeError(
                "google_sheets backend requires gspread and google-auth: "
                "pip install gspread google-auth (or rebuild the container)"
            ) from exc

        # 3. authenticate
        if auth_mode == "oauth_user":
            self._client = gspread.oauth(
                credentials_filename=google_client_secret_file,
                authorized_user_filename=google_token_file,
            )
        else:
            from google.oauth2.service_account import Credentials

            creds = Credentials.from_service_account_file(
                google_credentials_file, scopes=[SPREADSHEETS_SCOPE]
            )
            self._client = gspread.authorize(creds)

    def _get_worksheet(self):
        if self._worksheet is None:
            spreadsheet = self._client.open_by_key(self._spreadsheet_id)
            try:
                self._worksheet = spreadsheet.worksheet(self._worksheet_name)
            except Exception:
                logger.info("Worksheet %r not found, creating it", self._worksheet_name)
                self._worksheet = spreadsheet.add_worksheet(
                    title=self._worksheet_name, rows=1000, cols=len(self._columns)
                )
        return self._worksheet

    def _ensure_header(self) -> None:
        if self._header_ready:
            return
        worksheet = self._get_worksheet()
        if worksheet.row_values(1) != self._columns:
            worksheet.insert_row(self._columns, 1)
        self._header_ready = True

    def log(self, record: ConversationRecord) -> None:
        self._ensure_header()
        self._get_worksheet().append_row(
            record.values(self._columns), value_input_option="USER_ENTERED"
        )

    def close(self) -> None:
        pass


def create_backend(config: "ConversationLogConfig") -> RegistryBackend:
    """Build the backend selected by ``CONVERSATION_LOG_BACKEND``."""
    backend = (config.backend or "none").strip().lower()
    if backend == "none":
        return NoopBackend()
    if backend == "sqlite":
        return SqliteBackend(config.db_path, config.columns)
    if backend == "google_sheets":
        if not config.spreadsheet_id:
            raise ValueError("google_sheets backend requires CONVERSATION_LOG_SPREADSHEET_ID")
        auth_mode = (config.google_auth_mode or "service_account").strip().lower()
        return GoogleSheetsBackend(
            auth_mode=auth_mode,
            spreadsheet_id=config.spreadsheet_id,
            worksheet=config.worksheet,
            columns=config.columns,
            google_credentials_file=config.google_credentials_file,
            google_client_secret_file=config.google_client_secret_file,
            google_token_file=config.google_token_file or "data/gspread_authorized_user.json",
        )
    raise ValueError(f"Unknown CONVERSATION_LOG_BACKEND: {backend!r}")
