"""Meta Cloud API WhatsApp client.

Handles webhook verification and outbound messaging (text messages,
CTA URL buttons for redirects, read receipts).
"""

from __future__ import annotations

import hashlib
import hmac
import logging

import httpx

from src.config import WhatsAppConfig

logger = logging.getLogger(__name__)


class MetaWhatsAppError(RuntimeError):
    pass


class MetaWhatsAppClient:
    def __init__(self, config: WhatsAppConfig):
        self._config = config
        base = config.graph_base_url.rstrip("/")
        self._api_url = (
            f"{base}/{config.api_version}/{config.phone_number_id}/messages"
        )
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {config.access_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def configured(self) -> bool:
        return bool(self._config.access_token and self._config.phone_number_id)

    @property
    def signature_enabled(self) -> bool:
        return bool(self._config.app_secret)

    def verify_webhook(self, mode: str | None, verify_token: str | None, challenge: str | None) -> str | None:
        """Return the challenge string when the verification request is valid."""
        if mode == "subscribe" and verify_token == self._config.verify_token:
            return challenge
        return None

    def verify_signature(self, raw_body: bytes, signature_header: str | None) -> bool:
        """Verify Meta's ``X-Hub-Signature-256`` (HMAC-SHA256 over the raw body).

        Uses the App Secret from ``WHATSAPP_APP_SECRET``. Returns ``False`` when
        the header is missing or does not match.
        """
        if not signature_header:
            return False
        try:
            _, _, digest = signature_header.partition("=")
            expected = hmac.new(
                self._config.app_secret.encode(), raw_body, hashlib.sha256
            ).hexdigest()
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(digest.lower(), expected)

    async def send_text(self, to: str, body: str) -> dict:
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        return await self._post(payload)

    async def send_cta_url(self, to: str, body: str, url: str, button_text: str = "Open") -> dict:
        """Send a text message with a tappable button pointing to ``url``.

        Used to redirect clients to a website or to a wa.me link for another
        WhatsApp vendor.
        """
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "button": "open_link",
                    "buttons": [{"type": "url", "title": button_text, "url": url}],
                },
            },
        }
        return await self._post(payload)

    async def mark_read(self, message_id: str) -> None:
        payload = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
        try:
            await self._post(payload)
        except MetaWhatsAppError:
            logger.warning("Could not mark message %s as read", message_id, exc_info=True)

    async def _post(self, payload: dict) -> dict:
        response = await self._client.post(self._api_url, json=payload)
        if response.status_code >= 400:
            logger.error("Meta API error %s: %s", response.status_code, response.text)
            raise MetaWhatsAppError(f"Meta API error {response.status_code}")
        return response.json()
