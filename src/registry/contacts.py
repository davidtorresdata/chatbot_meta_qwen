"""Optional contact directory used to enrich logged records.

WhatsApp only provides the phone number, so ``nombre`` / ``ciudad`` /
``empresa`` are resolved from a small JSON file you control. Point
``CONVERSATION_LOG_CONTACTS_FILE`` at a file like:

.. code-block:: json

    {
      "573001234567": {"nombre": "Juan Perez", "ciudad": "Bogota", "empresa": "Fertrac"},
      "573008887766": {"nombre": "Ana Gomez",  "ciudad": "Medellin", "empresa": "ACME"}
    }

Phone keys are matched as written and also after stripping every non-digit
character, so ``+57 300 123 4567`` finds ``573001234567``. Unknown numbers
produce empty strings for those three fields.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ContactDirectory:
    def __init__(self, path: str | Path | None = None):
        self._contacts: dict[str, dict[str, str]] = {}
        if path:
            self.load(path)

    def load(self, path: str | Path) -> None:
        p = Path(path)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            logger.exception("Could not read contacts file %s", p)
            return
        if not isinstance(data, dict):
            logger.warning("Contacts file %s must contain a JSON object", p)
            return
        self._contacts = {
            str(key): {str(field): str(value) for field, value in (item.items() if isinstance(item, dict) else [])}
            for key, item in data.items()
        }

    def lookup(self, phone: str) -> dict[str, str]:
        """Return the nombre/ciudad/empresa dict for ``phone`` or an empty dict."""
        if phone in self._contacts:
            return self._contacts[phone]
        digits = "".join(ch for ch in phone if ch.isdigit())
        if digits in self._contacts:
            return self._contacts[digits]
        return {}
