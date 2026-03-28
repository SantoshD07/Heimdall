from __future__ import annotations

import os

from supabase import create_client, Client

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        _client = create_client(url, key)
    return _client


def insert_raw(
    *,
    user_id: int,
    content_type: str,
    raw_content: str | None = None,
    file_id: str | None = None,
) -> dict:
    row = {
        "user_id": user_id,
        "content_type": content_type,
        "raw_content": raw_content,
        "file_id": file_id,
        "status": "pending",
    }
    result = _get_client().table("raw_saves").insert(row).execute()
    return result.data[0]
