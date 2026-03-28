"""
celery_app.py — Celery application factory for Heimdall.

Celery is the task queue that runs all heavy processing outside the FastAPI
request cycle.  When the Telegram webhook receives a message it saves the raw
content to Supabase and immediately returns 200 OK to Telegram.  The actual
work (URL fetching, OCR, AI classification) is handed off to a Celery worker
process through Redis.

Why Redis as the broker?
    Redis acts as the message broker — it holds tasks in a queue until a worker
    picks them up.  It also stores task results (result backend) so we can check
    whether a task succeeded or failed.

Process model:
    ┌─────────────┐   enqueue    ┌────────┐   dequeue   ┌────────────────┐
    │  FastAPI    │ ──────────►  │ Redis  │ ──────────► │ Celery worker  │
    │  (webhook)  │              │ broker │             │ (process_save) │
    └─────────────┘              └────────┘             └────────────────┘

Starting workers (separate terminal):
    celery -A celery_app worker --loglevel=info --concurrency=4

    On Windows, billiard cannot fork processes so use --pool=solo:
    celery -A celery_app worker --loglevel=info --pool=solo
"""

from __future__ import annotations

import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# App instance
# ---------------------------------------------------------------------------

#: The single Celery application used across the whole project.
#: ``broker`` is where tasks are sent (Redis list).
#: ``backend`` is where results are stored so callers can inspect task state.
celery = Celery(
    "heimdall",
    broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    include=["pipeline.tasks"],  # modules that contain @celery.task definitions
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

celery.conf.update(
    # Serialize task arguments as JSON (human-readable, safe).
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Store task results for 24 hours then discard.
    result_expires=86_400,
    # Acknowledge the task only after it finishes, not when it's received.
    # This means a crashed worker won't silently drop a task.
    task_acks_late=True,
    # If a worker crashes mid-task, put the task back in the queue once.
    task_reject_on_worker_lost=True,
    timezone="UTC",
    # On Windows, billiard cannot fork processes — solo pool runs tasks in the
    # same process to avoid the ValueError('not enough values to unpack') error.
    worker_pool="solo",
)
