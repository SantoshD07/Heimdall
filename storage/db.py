"""
storage/db.py — Supabase database helpers for Heimdall.

All reads and writes to Supabase go through this module.  Nothing outside
this file should import the Supabase client directly.

Two-table pattern:
    raw_saves        — immutable inbox; written instantly when a message
                       arrives, before any processing starts.  The pipeline
                       only updates the ``status`` and ``error_msg`` columns.
    classified_saves — enriched knowledge; written only after the full
                       pipeline (extraction + classification) succeeds.
                       References raw_saves.id as a foreign key.

Client initialisation:
    The Supabase client is created lazily on first use and reused for the
    lifetime of the process.  Environment variables are read at call time so
    the module can be imported before .env is loaded.
"""

from __future__ import annotations

import os

from supabase import create_client, Client

_client: Client | None = None


def _get_client() -> Client:
    """Return the shared Supabase client, creating it on first call."""
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        _client = create_client(url, key)
    return _client


# ---------------------------------------------------------------------------
# raw_saves
# ---------------------------------------------------------------------------

def insert_raw(
    *,
    user_id: int,
    content_type: str,
    raw_content: str | None = None,
    file_id: str | None = None,
) -> dict:
    """
    Insert a new row into ``raw_saves`` with status 'pending'.

    Called immediately when a Telegram message arrives — before any processing
    so the save is never lost even if the pipeline fails.

    Args:
        user_id:      Telegram user ID of the sender.
        content_type: One of 'url', 'screenshot', or 'note'.
        raw_content:  The raw text or URL (None for screenshots).
        file_id:      Telegram file_id (only set for screenshots).

    Returns:
        The inserted row as a dict (includes the generated ``id`` UUID).
    """
    row = {
        "user_id": user_id,
        "content_type": content_type,
        "raw_content": raw_content,
        "file_id": file_id,
        "status": "pending",
    }
    result = _get_client().table("raw_saves").insert(row).execute()
    return result.data[0]


def get_raw_save(raw_id: str) -> dict | None:
    """
    Fetch a single row from ``raw_saves`` by its UUID.

    Args:
        raw_id: UUID string of the row.

    Returns:
        Row dict if found, None if the row does not exist.
    """
    result = (
        _get_client()
        .table("raw_saves")
        .select("*")
        .eq("id", raw_id)
        .maybe_single()
        .execute()
    )
    return result.data


def update_raw_status(raw_id: str, status: str, *, error_msg: str | None = None) -> None:
    """
    Update the ``status`` (and optionally ``error_msg``) of a ``raw_saves`` row.

    Called by the Celery task to track pipeline progress:
        pending     → processing  (task picked up the row)
        processing  → done        (pipeline completed successfully)
        processing  → failed      (all retries exhausted)

    Args:
        raw_id:    UUID of the row to update.
        status:    New status value ('processing', 'done', or 'failed').
        error_msg: Optional error description stored when status='failed'.
    """
    patch = {"status": status}
    if error_msg is not None:
        patch["error_msg"] = error_msg
    _get_client().table("raw_saves").update(patch).eq("id", raw_id).execute()


def get_failed_saves(*, max_retries: int = 3) -> list:
    """
    Return all failed raw_saves rows that are still under the retry limit.

    Used by the Celery beat task to re-enqueue saves that failed transiently.

    Args:
        max_retries: Rows with retry_count >= this value are excluded.

    Returns:
        List of row dicts.
    """
    return (
        _get_client()
        .table("raw_saves")
        .select("*")
        .eq("status", "failed")
        .lt("retry_count", max_retries)
        .execute()
        .data
    )


# ---------------------------------------------------------------------------
# classified_saves
# ---------------------------------------------------------------------------

def insert_classified(
    *,
    raw_id: str,
    user_id: int,
    title: str,
    summary: str,
    key_insight: str,
    category: str,
    tags: list[str],
    full_text: str,
    domain: str | None = None,
) -> dict:
    """
    Insert a new row into ``classified_saves``.

    Only called after the full pipeline succeeds.  Linked to the original
    raw_saves row via ``raw_id``.

    Args:
        raw_id:      UUID of the parent raw_saves row.
        user_id:     Telegram user ID.
        title:       Short descriptive title from the classifier.
        summary:     Two-sentence summary.
        key_insight: Single-sentence key takeaway.
        category:    One of the fixed category list.
        tags:        List of 2–4 lowercase tag strings.
        full_text:   First 5000 chars of extracted content.
        domain:      Domain name (URLs only, None for notes/screenshots).

    Returns:
        The inserted row as a dict.
    """
    row = {
        "raw_id": raw_id,
        "user_id": user_id,
        "title": title,
        "summary": summary,
        "key_insight": key_insight,
        "category": category,
        "tags": tags,
        "full_text": full_text,
        "domain": domain,
    }
    result = _get_client().table("classified_saves").insert(row).execute()
    return result.data[0]


def get_recent(*, user_id: int, n: int = 5) -> list:
    """
    Return the n most recently classified saves for a user.

    Args:
        user_id: Telegram user ID.
        n:       Maximum number of rows to return.

    Returns:
        List of classified_saves row dicts, newest first.
    """
    return (
        _get_client()
        .table("classified_saves")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(n)
        .execute()
        .data
    )


def get_by_category(*, user_id: int, category: str) -> list:
    """
    Return all classified saves for a user in the given category.

    Case-insensitive match via ilike.

    Args:
        user_id:  Telegram user ID.
        category: Category string to filter by (e.g. 'Tech').

    Returns:
        List of classified_saves row dicts, newest first.
    """
    return (
        _get_client()
        .table("classified_saves")
        .select("*")
        .eq("user_id", user_id)
        .ilike("category", category)
        .order("created_at", desc=True)
        .execute()
        .data
    )


def search_saves(*, user_id: int, query: str, limit: int = 5) -> list:
    """
    Full-text search across title, summary, and key_insight.

    Requires a GIN tsvector index on those columns in Supabase (see schema SQL
    in docs/heimdall_revised_impl_plan.md).

    Args:
        user_id: Telegram user ID.
        query:   Search query string.
        limit:   Maximum rows to return.

    Returns:
        List of matching classified_saves row dicts.
    """
    return (
        _get_client()
        .table("classified_saves")
        .select("*")
        .eq("user_id", user_id)
        .text_search("title,summary,key_insight", query, config="english")
        .limit(limit)
        .execute()
        .data
    )
