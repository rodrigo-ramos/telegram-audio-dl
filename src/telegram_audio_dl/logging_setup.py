"""Configuración del logging del CLI.

- Logs van a un archivo rotativo en `<state_dir>/logs/telegram_audio_dl.log`.
- Nivel configurable con env `LOG_LEVEL` (default INFO; DEBUG para troubleshooting).
- Captura también logs de `telethon` a partir de WARNING (errores de red, sesión).
- NO escribe a stdout para no contaminar la UI interactiva.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

LOGGER_NAME = "telegram_audio_dl"
_DEFAULT_FORMAT = (
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"
_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB
_BACKUP_COUNT = 5


def setup_logging(
    state_dir: Path,
    level_override: str | None = None,
    log_filename: str = "telegram_audio_dl.log",
) -> Path:
    """Configura logging rotativo. `log_filename` permite separar el log
    del daemon (`daemon.log`) del CLI interactivo (`telegram_audio_dl.log`)."""
    log_dir = state_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / log_filename

    level_name = (level_override or os.environ.get("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(_DEFAULT_FORMAT, datefmt=_DEFAULT_DATEFMT)
    handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)

    app_logger = logging.getLogger(LOGGER_NAME)
    if not _has_our_handler(app_logger, log_file):
        app_logger.setLevel(level)
        app_logger.addHandler(handler)
        app_logger.propagate = False

    # Telethon: WARNING+ al mismo archivo (FloodWait, sesiones, errores de red)
    telethon_logger = logging.getLogger("telethon")
    if not _has_our_handler(telethon_logger, log_file):
        telethon_logger.setLevel(logging.WARNING)
        telethon_logger.addHandler(handler)
        telethon_logger.propagate = False

    return log_file


def _has_our_handler(logger: logging.Logger, log_file: Path) -> bool:
    for h in logger.handlers:
        if isinstance(h, logging.handlers.RotatingFileHandler):
            try:
                if Path(h.baseFilename) == log_file:
                    return True
            except Exception:
                pass
    return False


def get_logger(name: str) -> logging.Logger:
    if name.startswith(LOGGER_NAME):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
