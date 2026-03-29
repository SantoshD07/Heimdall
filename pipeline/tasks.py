"""
pipeline/tasks.py — Celery tasks for the Heimdall processing pipeline.

Flow per task:
    1. Fetch raw_saves row from Supabase.
    2. Mark status → 'processing'.
    3. Extract text based on content_type (url / screenshot / note).
    4. Classify extracted text with Gemini → structured dict.
    5. Write to classified_saves.
    6. Mark status → 'done'.
    7. Send Telegram confirmation to the user.

On any exception, status → 'failed' and Celery retries with exponential
backoff (2^retry_count seconds, max 3 retries).
"""

from __future__ import annotations

import logging
import os

import httpx
from celery_app import celery
from pipeline.classifier import classify
from pipeline.extractor import extract_note, extract_screenshot, extract_url
from storage.db import get_raw_save, insert_classified, update_raw_status

logger = logging.getLogger(__name__)


@celery.task(
    bind=True,
    max_retries=3,
    default_retry_delay=2,
    name="pipeline.process_save",
)
def process_save(self, raw_id: str) -> None:
    """
    Process a single saved item end-to-end.

    Args:
        raw_id: UUID of the raw_saves row to process.
    """
    logger.info("[%s] Starting pipeline", raw_id)

    try:
        row = get_raw_save(raw_id)
        if row is None:
            logger.error("[%s] Row not found, skipping", raw_id)
            return

        update_raw_status(raw_id, "processing")
        content_type = row["content_type"]
        domain = None

        # ── Step 4: extract ──────────────────────────────────────────────────
        if content_type == "url":
            text, domain = extract_url(row["raw_content"])
        elif content_type == "screenshot":
            text = extract_screenshot(row["file_id"])
        elif content_type == "note":
            text = extract_note(row["raw_content"])
        else:
            logger.warning("[%s] Unknown content_type=%s", raw_id, content_type)
            text = row.get("raw_content") or ""

        logger.info("[%s] Extracted %d chars (type=%s)", raw_id, len(text), content_type)

        # ── Step 5: classify ─────────────────────────────────────────────────
        classified = classify(text=text, content_type=content_type)
        logger.info("[%s] Classified → %s", raw_id, classified["category"])

        # ── Persist ──────────────────────────────────────────────────────────
        insert_classified(
            raw_id=raw_id,
            user_id=row["user_id"],
            domain=domain,
            full_text=text,
            **classified,
        )
        update_raw_status(raw_id, "done")

        # ── Notify user ───────────────────────────────────────────────────────
        from bot.replies import fmt_save
        _send_telegram(row["user_id"], fmt_save(classified))
        logger.info("[%s] Done", raw_id)

    except Exception as exc:
        logger.warning(
            "[%s] Error: %s — retry %d/%d",
            raw_id, exc, self.request.retries, self.max_retries,
        )
        update_raw_status(raw_id, "failed", error_msg=str(exc))
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)


def _send_telegram(user_id: int, text: str) -> None:
    """
    Send a message to a Telegram user via direct HTTP call.

    Used from the Celery worker, which has no bot event loop.
    The worker communicates back to the user by POSTing directly to the
    Telegram Bot API.

    Args:
        user_id: Telegram chat ID (same as user ID for private chats).
        text:    Message text (Markdown formatted).
    """
    token = os.environ["BOT_TOKEN"]
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": user_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Failed to send Telegram reply to %s: %s", user_id, exc)
