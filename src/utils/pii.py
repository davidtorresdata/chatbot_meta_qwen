"""PII helpers for logs (Ley 1581 de 2012 - habeas data).

Phone numbers are masked in every log line; message contents are only logged
when ``LOG_MESSAGE_CONTENT=1`` (intended for local debugging, never production).
"""

from __future__ import annotations

import os


def mask_phone(phone: str | None) -> str:
    """``573001234567`` -> ``5730*****567``. Keeps enough to correlate, not to identify."""
    if not phone:
        return "-"
    digits = str(phone)
    if len(digits) <= 6:
        return "*" * len(digits)
    return f"{digits[:4]}{'*' * (len(digits) - 7)}{digits[-3:]}"


def log_content_enabled() -> bool:
    return os.getenv("LOG_MESSAGE_CONTENT", "").strip().lower() in ("1", "true", "yes", "on")


def content_for_log(text: str | None, limit: int = 120) -> str:
    """Return the text for logs only when explicitly enabled, else its length."""
    if text is None:
        return "-"
    if not log_content_enabled():
        return f"<{len(text)} chars>"
    return repr(text[:limit] + ("..." if len(text) > limit else ""))
