"""Data model for a logged conversation turn."""

from __future__ import annotations

from dataclasses import asdict, dataclass

DEFAULT_COLUMNS = [
    "hora",
    "numero",
    "nombre",
    "ciudad",
    "empresa",
    "pregunta",
    "respuesta",
]


@dataclass
class ConversationRecord:
    """One persisted interaction between a contact and the bot.

    ``hora`` is an ISO-8601 timestamp (see ``CONVERSATION_LOG_TIMEZONE``).
    ``numero`` is the WhatsApp number as sent by Meta (``message.from``).
    ``nombre`` / ``ciudad`` / ``empresa`` are enriched from the contact
    directory when available (see ``src/registry/contacts.py``).
    """

    hora: str
    numero: str
    nombre: str = ""
    ciudad: str = ""
    empresa: str = ""
    pregunta: str = ""
    respuesta: str = ""

    def values(self, columns: list[str]) -> list[str]:
        """Field values in the order given by ``columns`` (unknown -> '')."""
        data = asdict(self)
        return [str(data.get(col, "")) for col in columns]
