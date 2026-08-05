"""Tests for config loading and env overrides."""

from pathlib import Path

import pytest

from src.config import PROJECT_ROOT, Settings, load_settings


def test_default_config_loads():
    settings = load_settings(PROJECT_ROOT / "config" / "config.yaml")
    assert settings.app.name
    assert settings.llm.model
    assert 0.0 <= settings.llm.temperature <= 1.0
    assert settings.storage.table_name == "knowledge_base"
    assert settings.redirects.rules


def test_yaml_roundtrip():
    import yaml

    with open(PROJECT_ROOT / "config" / "config.yaml", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    settings = Settings.model_validate(data)
    assert settings.agent.history_size > 0
    assert settings.redirects.default_url


def test_temperature_env_override(monkeypatch):
    monkeypatch.setenv("TEMPERATURE", "0.7")
    settings = load_settings(PROJECT_ROOT / "config" / "config.yaml")
    assert settings.llm.temperature == 0.7


def test_llm_model_env_override(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "Qwen/Qwen2.5-72B-Instruct")
    settings = load_settings(PROJECT_ROOT / "config" / "config.yaml")
    assert settings.llm.model == "Qwen/Qwen2.5-72B-Instruct"


def test_invalid_temperature_rejected():
    with pytest.raises(Exception):
        Settings.model_validate({"llm": {"temperature": 5.0}})


def test_config_path_env(monkeypatch):
    monkeypatch.setenv("CONFIG_PATH", str(Path(PROJECT_ROOT) / "config" / "config.yaml"))
    settings = load_settings()
    assert settings.storage.table_name == "knowledge_base"
