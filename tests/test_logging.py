"""Tests for the persistent action-log setup."""

import logging
import re

import pytest

import src.utils.logging as logging_mod


@pytest.fixture()
def isolated_logger():
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    root.handlers[:] = []
    yield root
    root.handlers[:] = old_handlers


def test_log_file_created_with_expected_name(isolated_logger, tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    logging_mod.setup_logging()

    logging.getLogger("test.actions").info("Inbound action | phone=1555 text='hi'")

    files = list(tmp_path.glob("wa_ollama_logs_*.txt"))
    assert len(files) == 1
    assert re.fullmatch(r"wa_ollama_logs_\d{8}_\d{6}_\d{6}\.txt", files[0].name)

    content = files[0].read_text(encoding="utf-8")
    assert "Inbound action | phone=1555" in content
    assert "INFO" in content


def test_log_file_contains_error_level(isolated_logger, tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    logging_mod.setup_logging()

    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("test.actions").exception("Action failed | phone=1555")

    files = list(tmp_path.glob("wa_ollama_logs_*.txt"))
    content = files[0].read_text(encoding="utf-8")
    assert "Action failed | phone=1555" in content
    assert "Traceback" in content


def test_fresh_timestamped_file_per_startup(isolated_logger, tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))

    logging_mod.setup_logging()
    logging.getLogger("test").info("first run")

    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    logging_mod.setup_logging()
    logging.getLogger("test").info("second run")

    files = list(tmp_path.glob("wa_ollama_logs_*.txt"))
    assert len(files) == 2
