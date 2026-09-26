"""Logging setup.

Writes every application log both to the console and to a persistent file:

    <LOG_DIR>/wa_ollama_logs_<YYYYMMDD_HHMMSS>.txt

The timestamp is fixed when the app starts, so each run produces its own file.

Retention (habeas data + disk safety):
  * each file rotates at ``LOG_MAX_BYTES`` (default 20 MB) keeping
    ``LOG_BACKUP_COUNT`` backups (default 5);
  * at startup, log files older than ``LOG_RETENTION_DAYS`` (default 30) are purged.

Phone numbers are masked and message texts are not logged unless
``LOG_MESSAGE_CONTENT=1`` (see src/utils/pii.py).
Set ``LOG_DIR`` to override the folder (default: ``<project root>/logs``).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _log_file_path(log_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return log_dir / f"wa_ollama_logs_{timestamp}.txt"


def _file_handler(log_dir: Path) -> logging.Handler:
    log_dir.mkdir(parents=True, exist_ok=True)
    _purge_old_logs(log_dir)
    return RotatingFileHandler(
        _log_file_path(log_dir),
        maxBytes=int(os.getenv("LOG_MAX_BYTES", 20 * 1024 * 1024)),
        backupCount=int(os.getenv("LOG_BACKUP_COUNT", 5)),
        encoding="utf-8",
    )


def _purge_old_logs(log_dir: Path) -> None:
    days = float(os.getenv("LOG_RETENTION_DAYS", 30))
    if days <= 0:
        return
    cutoff = time.time() - days * 86_400
    for path in log_dir.glob("wa_ollama_logs_*.txt*"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


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
        handlers.append(_file_handler(log_dir))
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
        file_handler = _file_handler(log_dir)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    except OSError as exc:
        logging.getLogger(__name__).warning("Could not create file log: %s", exc)
