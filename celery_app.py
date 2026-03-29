"""
celery_app.py — Celery application factory for Heimdall.

Celery is the task queue that runs all heavy processing outside the FastAPI
request cycle.  When the Telegram webhook receives a message it saves the raw
content to Supabase and immediately returns 200 OK to Telegram.  The actual
work (URL fetching, OCR, AI classification) is handed off to a Celery worker
process through Redis.

Process model:
    ┌─────────────┐   enqueue    ┌────────┐   dequeue   ┌────────────────┐
    │  FastAPI    │ ──────────►  │ Redis  │ ──────────► │ Celery worker  │
    │  (webhook)  │              │ broker │             │ (process_save) │
    └─────────────┘              └────────┘             └────────────────┘

Starting the worker (pass --logfile so all pipeline logs go to the file):
    celery -A celery_app worker --loglevel=info --pool=solo --logfile=logs/worker.log
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# Ensure the project root is on sys.path so that 'bot', 'pipeline',
# 'storage' are importable when the worker process starts.
_project_root = Path(__file__).parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Ensure logs/ exists before Celery tries to open the --logfile path.
(_project_root / "logs").mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# App instance
# ---------------------------------------------------------------------------

celery = Celery(
    "heimdall",
    broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    include=["pipeline.tasks"],
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=86_400,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    timezone="UTC",
    worker_pool="solo",
    # Log format used when --logfile is passed to the worker.
    # All loggers (including pipeline.*) propagate to root and use this format.
    worker_log_format="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
    worker_task_log_format="%(asctime)s  %(levelname)-8s  [%(task_name)s|%(task_id)s]  %(message)s",
)
