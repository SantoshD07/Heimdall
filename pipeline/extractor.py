"""
pipeline/extractor.py — Content extraction for each save type.

Three branches, each returning plain text:
    url        — fetch the page with Trafilatura, return clean article text
    note       — strip whitespace, return as-is
    screenshot — download image bytes from Telegram CDN, send to Gemini Vision
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import google.generativeai as genai
import httpx
import trafilatura

logger = logging.getLogger(__name__)

_BOT_TOKEN = None


def _bot_token() -> str:
    global _BOT_TOKEN
    if _BOT_TOKEN is None:
        _BOT_TOKEN = os.environ["BOT_TOKEN"]
    return _BOT_TOKEN


def extract_url(url: str) -> tuple[str, str]:
    """
    Fetch a URL and extract clean article text with Trafilatura.

    Args:
        url: The URL to fetch.

    Returns:
        (text, domain) — extracted text (up to 5000 chars) and the domain.
        text is empty string if the page could not be fetched or parsed.
    """
    domain = urlparse(url).netloc.replace("www.", "")
    logger.info("[extractor] Fetching URL: %s", url)
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            logger.warning("[extractor] trafilatura.fetch_url returned empty for %s", url)
            return "", domain
        text = trafilatura.extract(downloaded) or ""
        if not text:
            logger.warning("[extractor] trafilatura.extract returned empty for %s", url)
        else:
            logger.info("[extractor] URL extraction OK — %d chars from %s", len(text), domain)
        return text[:5000], domain
    except Exception as exc:
        logger.warning("[extractor] URL extraction failed for %s: %s", url, exc)
        return "", domain


def extract_note(raw_content: str) -> str:
    """
    Clean and return plain-text note content.

    Normalises whitespace; truncates to 5000 chars to match the URL branch cap.

    Args:
        raw_content: The raw note text from the Telegram message.

    Returns:
        Cleaned text, up to 5000 characters.
    """
    text = " ".join(raw_content.split())[:5000]
    logger.info("[extractor] Note passthrough — %d chars", len(text))
    return text


def extract_screenshot(file_id: str) -> str:
    """
    Download a Telegram photo and extract text with Gemini Vision.

    Steps:
        1. Call getFile to resolve file_id → file_path on Telegram CDN.
        2. Download the image bytes.
        3. Send to Gemini 2.0 Flash with an OCR prompt.

    Args:
        file_id: Permanent Telegram file_id for the photo.

    Returns:
        Extracted text string; empty string if no text detected or on error.
    """
    token = _bot_token()
    logger.info("[extractor] Screenshot OCR — resolving file_id=%s", file_id)
    try:
        # Resolve file_id to a download path
        meta = httpx.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
            timeout=15,
        )
        meta.raise_for_status()
        file_path = meta.json()["result"]["file_path"]
        logger.info("[extractor] Screenshot file_path=%s", file_path)

        # Download image bytes
        img_resp = httpx.get(
            f"https://api.telegram.org/file/bot{token}/{file_path}",
            timeout=30,
        )
        img_resp.raise_for_status()
        image_bytes = img_resp.content
        logger.info("[extractor] Downloaded %d bytes for OCR", len(image_bytes))

        # Gemini Vision OCR
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content([
            "Extract all text visible in this image verbatim. "
            "Return only the extracted text with no commentary.",
            {"mime_type": "image/jpeg", "data": image_bytes},
        ])
        text = (response.text or "").strip()
        if text:
            logger.info("[extractor] OCR OK — %d chars extracted", len(text))
        else:
            logger.warning("[extractor] OCR returned empty text for file_id=%s", file_id)
        return text

    except Exception as exc:
        logger.warning("[extractor] Screenshot extraction failed for file_id=%s: %s", file_id, exc)
        return ""
