"""
pipeline/tasks.py — Celery tasks for the Heimdall processing pipeline.

This module defines the async work that happens *after* a message has been
saved to ``raw_saves``.  The FastAPI webhook handler calls
``process_save.delay(raw_id)`` to enqueue the task and immediately returns;
the actual processing runs here in a separate worker process.

Current state (Step 3a):
    The task is wired and logs what it would do, but no extraction or
    classification happens yet.  Subsequent steps will fill in each branch:

    Step 4  — URL branch: fetch page text with Trafilatura
    Step 4  — Screenshot branch: download image via file_id, run Google Vision OCR
    Step 4  — Note branch: passthrough / light clean
    Step 5  — Classifier agent: structure extracted text → classified_saves

Retry strategy:
    Celery retries automatically on any exception, up to ``max_retries`` times
    with exponential backoff (2^retry_count seconds).  This handles transient
    network errors without manual intervention.

    Retry #1 → 2s delay
    Retry #2 → 4s delay
    Retry #3 → 8s delay
    After 3 failures the task is marked FAILED and the row status set to
    'failed' in Supabase so it can be inspected or re-queued manually.
"""

from __future__ import annotations

import logging

from celery_app import celery
from storage.db import get_raw_save, update_raw_status

logger = logging.getLogger(__name__)


@celery.task(
    bind=True,           # ``self`` gives access to retry / request metadata
    max_retries=3,
    default_retry_delay=2,
    name="pipeline.process_save",
)
def process_save(self, raw_id: str) -> None:
    """
    Entry point for processing a single saved item.

    Fetches the ``raw_saves`` row by ``raw_id``, branches on ``content_type``,
    and will eventually extract text and trigger classification.

    Args:
        raw_id: UUID of the ``raw_saves`` row to process.

    Raises:
        celery.exceptions.Retry: transparently raised by ``self.retry()`` when
            a retryable error occurs — Celery catches this internally.
    """
    logger.info("[%s] Starting processing", raw_id)

    try:
        row = get_raw_save(raw_id)

        if row is None:
            # Row missing — nothing to retry, just bail out.
            logger.error("[%s] Row not found in raw_saves, skipping", raw_id)
            return

        update_raw_status(raw_id, "processing")
        content_type = row["content_type"]

        logger.info("[%s] content_type=%s", raw_id, content_type)

        if content_type == "url":
            # Step 4: fetch page text with Trafilatura
            logger.info("[%s] TODO: fetch URL and extract text", raw_id)

        elif content_type == "screenshot":
            # Step 4: download image via file_id → Google Vision OCR
            logger.info("[%s] TODO: download image and run OCR", raw_id)

        elif content_type == "note":
            # Step 4: passthrough — text is already in raw_content
            logger.info("[%s] TODO: clean and pass through note text", raw_id)

        # Step 5: classifier agent will run here after extraction is wired
        logger.info("[%s] TODO: run classifier agent", raw_id)

        update_raw_status(raw_id, "done")
        logger.info("[%s] Processing complete", raw_id)

    except Exception as exc:
        logger.warning("[%s] Error: %s — retry %d/%d", raw_id, exc, self.request.retries, self.max_retries)
        update_raw_status(raw_id, "failed", error_msg=str(exc))
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
