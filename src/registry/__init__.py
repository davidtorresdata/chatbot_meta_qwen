"""Conversation registry: who talked to the bot, when, and what was said.

Every incoming message that produces a reply is persisted as one record with
the fields configured in ``conversation_log.columns`` (defaults: hora, numero,
nombre, ciudad, empresa, pregunta, respuesta).

The destination is fully parametrizable through ``CONVERSATION_LOG_BACKEND``:

* ``none``          -> disabled (no-op)
* ``sqlite``        -> local SQLite file (stdlib, zero extra dependencies)
* ``google_sheets`` -> append rows to a Google Spreadsheet

See ``docs/CONVERSATION_REGISTRY.md`` for the full configuration guide.
"""

from src.registry.service import ConversationRegistry, build_registry

__all__ = ["ConversationRegistry", "build_registry"]
