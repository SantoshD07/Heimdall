"""
logging_config.py — Logging setup for the FastAPI web service.

Called once at startup in main.py.  Writes to logs/web.log (rotating).

The Celery worker does NOT use this — its logs are routed to logs/worker.log
via the --logfile flag passed on the command line:
    celery -A celery_app worker --loglevel=info --pool=solo --logfile=logs/worker.log
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def setup_logging(service: str) -> None:
    """
    Configure root logger for the given service.

    Creates logs/ directory if needed, attaches a rotating file handler
    (10 MB / 5 backups) and a stdout stream handler.

    Args:
        service: 'web' — determines the log file name (logs/web.log).
    """
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)

    log_file = logs_dir / f"{service}.log"

    fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialised — service=%s  file=%s", service, log_file
    )
