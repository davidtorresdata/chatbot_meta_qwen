"""Tests for the conversation registry (records, backends, enrichment)."""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from src.config import Settings
from src.registry.backends import NoopBackend, SqliteBackend, create_backend
from src.registry.contacts import ContactDirectory
from src.registry.models import ConversationRecord
from src.registry.service import ConversationRegistry, build_registry

COLUMNS = ["hora", "numero", "nombre", "ciudad", "empresa", "pregunta", "respuesta"]


class CapturingBackend:
    def __init__(self):
        self.records: list[ConversationRecord] = []

    def log(self, record: ConversationRecord) -> None:
        self.records.append(record)

    def close(self) -> None:
        pass


# ------------------------------------------------------------------ backends
def test_sqlite_backend_roundtrip(tmp_path):
    path = tmp_path / "reg.sqlite3"
    backend = SqliteBackend(path, COLUMNS)
    record = ConversationRecord(
        hora="2026-08-04T10:00:00-05:00",
        numero="573001234567",
        nombre="Juan",
        ciudad="Bogota",
        empresa="Fertrac",
        pregunta="Hola",
        respuesta="Hola!",
    )
    backend.log(record)
    backend.close()

    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT hora, numero, nombre, ciudad, empresa, pregunta, respuesta FROM conversations"
    ).fetchall()
    conn.close()
    assert rows == [
        ("2026-08-04T10:00:00-05:00", "573001234567", "Juan", "Bogota", "Fertrac", "Hola", "Hola!")
    ]


def test_sqlite_backend_custom_columns(tmp_path):
    path = tmp_path / "mini.sqlite3"
    backend = SqliteBackend(path, ["hora", "numero", "pregunta"])
    backend.log(ConversationRecord(hora="2026-08-04T10:00:00Z", numero="1", pregunta="Hola"))
    backend.close()

    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT * FROM conversations").fetchall()
    conn.close()
    assert rows == [("2026-08-04T10:00:00Z", "1", "Hola")]


def test_sqlite_missing_column_becomes_empty(tmp_path):
    path = tmp_path / "mini2.sqlite3"
    backend = SqliteBackend(path, ["hora", "numero", "pregunta"])
    backend.log(ConversationRecord(hora="2026-08-04T10:00:00Z", numero="1", pregunta="Hola"))
    backend.close()

    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT * FROM conversations").fetchall()
    conn.close()
    assert len(rows) == 1
    assert len(rows[0]) == 3


def test_noop_backend_swallows():
    NoopBackend().log(ConversationRecord(hora="x", numero="1"))


# -------------------------------------------------------------- contacts dir
def test_contacts_lookup(tmp_path):
    path = tmp_path / "contacts.json"
    path.write_text(
        json.dumps(
            {
                "573001234567": {"nombre": "Ana", "ciudad": "Medellin", "empresa": "ACME"},
                "no-info": {"foo": "bar"},
            }
        ),
        encoding="utf-8",
    )
    directory = ContactDirectory(path)
    assert directory.lookup("573001234567")["ciudad"] == "Medellin"
    # keys are matched after stripping every non-digit character
    assert directory.lookup("+57 300 123 4567")["nombre"] == "Ana"
    assert directory.lookup("573001234567")["empresa"] == "ACME"
    assert directory.lookup("9999999999") == {}


def test_contacts_missing_file(tmp_path, caplog):
    directory = ContactDirectory(tmp_path / "nope.json")
    assert directory.lookup("1") == {}


# ------------------------------------------------------------------ service
def test_service_enriches_and_logs(tmp_path):
    contacts = tmp_path / "contacts.json"
    contacts.write_text(
        json.dumps({"573001234567": {"nombre": "Ana", "ciudad": "Medellin", "empresa": "ACME"}}),
        encoding="utf-8",
    )
    config = Settings().conversation_log
    config.enabled = True
    config.backend = "sqlite"
    config.db_path = str(tmp_path / "reg.sqlite3")
    config.contacts_file = str(contacts)

    registry = build_registry(Settings(conversation_log=config))
    assert registry is not None
    asyncio.run(registry.log_turn("573001234567", "Hola", "Hola!"))
    registry.close()

    conn = sqlite3.connect(tmp_path / "reg.sqlite3")
    row = conn.execute("SELECT numero, nombre, ciudad, empresa, pregunta, respuesta FROM conversations").fetchone()
    conn.close()
    assert row == ("573001234567", "Ana", "Medellin", "ACME", "Hola", "Hola!")


def test_build_registry_disabled_by_default():
    settings = Settings()
    assert settings.conversation_log.enabled is False
    assert build_registry(settings) is None


def test_timezone_used_for_hora():
    config = Settings().conversation_log
    config.enabled = True
    config.timezone = "America/Bogota"
    backend = CapturingBackend()
    registry = ConversationRegistry(config, backend)

    asyncio.run(registry.log_turn("1", "Hola", "Hola!"))
    assert backend.records[0].hora.endswith("-05:00")


# ------------------------------------------------------------------- factory
def test_google_sheets_requires_spreadsheet_id():
    config = Settings().conversation_log
    config.backend = "google_sheets"
    with pytest.raises(ValueError):
        create_backend(config)


def test_google_sheets_service_account_requires_credentials_file():
    config = Settings().conversation_log
    config.backend = "google_sheets"
    config.spreadsheet_id = "abc123"
    config.google_auth_mode = "service_account"
    config.google_credentials_file = ""
    with pytest.raises(ValueError):
        create_backend(config)


def test_oauth_user_requires_client_secret():
    config = Settings().conversation_log
    config.backend = "google_sheets"
    config.spreadsheet_id = "abc123"
    config.google_auth_mode = "oauth_user"
    config.google_client_secret_file = ""
    with pytest.raises(ValueError):
        create_backend(config)


def test_oauth_user_missing_token_raises(tmp_path):
    config = Settings().conversation_log
    config.backend = "google_sheets"
    config.spreadsheet_id = "abc123"
    config.google_auth_mode = "oauth_user"
    config.google_client_secret_file = str(tmp_path / "client_secret.json")
    config.google_token_file = str(tmp_path / "token.json")  # does not exist
    with pytest.raises(RuntimeError, match="sheets_oauth_setup"):
        create_backend(config)


def test_oauth_user_uses_token_and_client_secret(tmp_path, monkeypatch):
    secret = tmp_path / "client_secret.json"
    secret.write_text("{}", encoding="utf-8")
    token = tmp_path / "token.json"
    token.write_text("{}", encoding="utf-8")

    config = Settings().conversation_log
    config.backend = "google_sheets"
    config.spreadsheet_id = "abc123"
    config.google_auth_mode = "oauth_user"
    config.google_client_secret_file = str(secret)
    config.google_token_file = str(token)

    import gspread

    calls = {}

    def fake_oauth(**kwargs):
        calls.update(kwargs)
        return object()

    monkeypatch.setattr(gspread, "oauth", fake_oauth)

    backend = create_backend(config)
    assert calls["credentials_filename"] == str(secret)
    assert calls["authorized_user_filename"] == str(token)
    assert backend is not None


def test_unknown_backend_raises():
    config = Settings().conversation_log
    config.backend = "mongodb"
    with pytest.raises(ValueError):
        create_backend(config)


def test_record_values_order():
    record = ConversationRecord(
        hora="h", numero="n", nombre="no", ciudad="c", empresa="e", pregunta="p", respuesta="r"
    )
    assert record.values(["empresa", "numero", "pregunta"]) == ["e", "n", "p"]
    assert record.values(["unknown"]) == [""]
