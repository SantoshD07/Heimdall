"""
pipeline/classifier.py — AI classification of extracted content.

Sends clean text to Gemini 2.0 Flash and gets back a structured dict with
title, summary, key_insight, category, and tags.

Uses response_mime_type="application/json" so Gemini returns valid JSON
directly rather than freeform text.
"""

from __future__ import annotations

import json
import logging
import os

import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted

logger = logging.getLogger(__name__)

_CATEGORIES = [
    "Tech", "Health", "Finance", "Science", "Productivity",
    "Design", "Culture", "Society", "Food", "Travel", "Other",
]

_PROMPT_TEMPLATE = """\
You are a personal knowledge classifier. Given the content below, produce a \
JSON object with exactly these fields:

  title       — ≤10 words, descriptive title
  summary     — exactly 2 sentences, factual
  key_insight — the single most useful takeaway, 1 sentence
  category    — exactly one of: {categories}
  tags        — list of 2–4 lowercase strings, no spaces

Content type: {content_type}
Content:
{text}

Return only the JSON object. No markdown fences, no extra text.
"""


def classify(*, text: str, content_type: str) -> dict:
    """
    Classify extracted text and return a structured dict.

    Args:
        text:         Extracted plain text (up to 5000 chars).
        content_type: One of 'url', 'screenshot', 'note'.

    Returns:
        Dict with keys: title, summary, key_insight, category, tags.
        Falls back to safe defaults if the model returns invalid JSON.
    """
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(
        "gemini-1.5-flash",
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
        ),
    )

    if not text.strip():
        logger.warning("[classifier] Empty text — skipping Gemini call, using fallback")
        return _empty_classification(content_type)

    logger.info("[classifier] Sending %d chars to Gemini (type=%s)", len(text), content_type)
    prompt = _PROMPT_TEMPLATE.format(
        categories=", ".join(_CATEGORIES),
        content_type=content_type,
        text=text[:4000],
    )

    try:
        response = model.generate_content(prompt)
        logger.info("[classifier] Gemini response received (%d chars)", len(response.text or ""))
        result = json.loads(response.text)

        classified = {
            "title": str(result.get("title", "Untitled"))[:200],
            "summary": str(result.get("summary", ""))[:500],
            "key_insight": str(result.get("key_insight", ""))[:500],
            "category": result.get("category", "Other") if result.get("category") in _CATEGORIES else "Other",
            "tags": [str(t) for t in result.get("tags", [])][:4],
        }
        logger.info(
            "[classifier] Classification OK — title=%r  category=%s  tags=%s",
            classified["title"], classified["category"], classified["tags"],
        )
        return classified

    except ResourceExhausted as exc:
        # 429 — let Celery retry the whole task with backoff, don't silently swallow it
        logger.warning("[classifier] Gemini 429 rate limit — will retry via Celery: %s", exc)
        raise

    except Exception as exc:
        logger.warning("[classifier] Classification failed: %s — using fallback", exc)
        return _empty_classification(content_type)


def _empty_classification(content_type: str) -> dict:
    """Return a safe fallback classification when extraction or model fails."""
    return {
        "title": f"Saved {content_type}",
        "summary": "Content could not be classified.",
        "key_insight": "",
        "category": "Other",
        "tags": [],
    }
