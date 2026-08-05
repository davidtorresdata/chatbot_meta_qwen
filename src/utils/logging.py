"""Logging setup.

Writes every application log both to the console and to a persistent file:

    <LOG_DIR>/wa_ollama_logs_<YYYYMMDD_HHMMSS>.txt

The timestamp is fixed when the app starts, so each run produces its own file
and old files are never deleted. Set ``LOG_DIR`` to override the folder
(default: ``<project root>/logs``, mounted live in Docker as ``./logs``).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _log_file_path(log_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return log_dir / f"wa_ollama_logs_{timestamp}.txt"


def setup_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.getLogger().setLevel(level)

    if logging.getLogger().hasHandlers():
        # Root already configured (e.g. uvicorn started first, or a test).
        # Ensure our file handler is present as well.
        _ensure_file_handler(level)
        return

    handlers: list[logging.Handler] = [logging.StreamHandler()]

    log_dir = Path(os.getenv("LOG_DIR", PROJECT_ROOT / "logs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(_log_file_path(log_dir), encoding="utf-8")
        handlers.append(file_handler)
    except OSError as exc:
        logging.getLogger(__name__).warning("Could not create file log: %s", exc)

    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        handlers=handlers,
    )


def _ensure_file_handler(level: int) -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler):
            return
    log_dir = Path(os.getenv("LOG_DIR", PROJECT_ROOT / "logs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(_log_file_path(log_dir), encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    except OSError as exc:
        logging.getLogger(__name__).warning("Could not create file log: %s", exc)
