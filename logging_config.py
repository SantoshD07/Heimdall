"""
logging_config.py — Shared logging setup for Heimdall services.

Each service (web, worker) writes to its own rotating log file under logs/
and also outputs to stdout.  Call setup_logging() once at process startup.

Log files:
    logs/web.log    — FastAPI / webhook requests
    logs/worker.log — Celery task execution, extraction, classification
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path


def setup_logging(service: str) -> None:
    """
    Configure logging for the given service.

    Creates logs/ directory if needed, sets up a rotating file handler
    (10 MB max, 5 backups) and a stdout stream handler.

    Args:
        service: 'web' or 'worker' — determines the log file name.
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
        maxBytes=10 * 1024 * 1024,  # 10 MB
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

    # Silence noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialised — service=%s  file=%s", service, log_file
    )
