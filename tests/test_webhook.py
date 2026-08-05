"""Tests for the FastAPI webhook endpoints (verification + health + signatures)."""

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

APP_SECRET = "test_app_secret"


def sign(raw_body: bytes, secret: str = APP_SECRET) -> str:
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "test_verify_token")
    from src.main import create_app

    return TestClient(create_app())


@pytest.fixture()
def signed_client(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "test_verify_token")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", APP_SECRET)
    from src.main import create_app

    return TestClient(create_app())


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_webhook_verification_success(client):
    response = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "test_verify_token", "hub.challenge": "123456"},
    )
    assert response.status_code == 200
    assert response.text == "123456"


def test_webhook_verification_wrong_token(client):
    response = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "123456"},
    )
    assert response.status_code == 403


def test_webhook_receive_acknowledges(client):
    payload = {"entry": []}
    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "received"


def test_webhook_rejects_unsigned_when_secret_set(signed_client):
    response = signed_client.post("/webhook", json={"entry": []})
    assert response.status_code == 403


def test_webhook_rejects_bad_signature(signed_client):
    response = signed_client.post(
        "/webhook",
        content=b'{"entry": []}',
        headers={"X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert response.status_code == 403


def test_webhook_accepts_valid_signature(signed_client):
    raw = b'{"entry": []}'
    response = signed_client.post(
        "/webhook",
        content=raw,
        headers={"X-Hub-Signature-256": sign(raw)},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "received"
