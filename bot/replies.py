"""
bot/replies.py — Telegram message formatters for Heimdall.

All reply text is built here so handlers and tasks stay logic-only.
Markdown formatting follows Telegram's legacy Markdown mode
(single * for bold, _ for italic).
"""

from __future__ import annotations


def fmt_save(classified: dict) -> str:
    """
    Format a classified save as a Telegram reply.

    Example output:
        Saved ✓

        *How React Hooks Changed Frontend Development*
        _Tech · css-tricks.com_

        Hooks let you use state and lifecycle features without writing a class
        component, making function components the standard pattern.

        💡 The key shift is from class components to function components with hooks.

        #react #frontend #javascript

    Args:
        classified: Dict with title, summary, key_insight, category, tags,
                    and optionally domain.

    Returns:
        Markdown-formatted string ready to send as a Telegram message.
    """
    tags = " ".join(f"#{t}" for t in (classified.get("tags") or []))
    domain = classified.get("domain")
    source_line = f"_{classified['category']} · {domain}_" if domain else f"_{classified['category']}_"
    insight = classified.get("key_insight", "")
    insight_line = f"\n\n💡 {insight}" if insight else ""

    return (
        f"Saved ✓\n\n"
        f"*{classified['title']}*\n"
        f"{source_line}\n\n"
        f"{classified['summary']}"
        f"{insight_line}\n\n"
        f"{tags}"
    ).strip()


def fmt_list(results: list) -> str:
    """
    Format a list of classified saves for /recent, /list, /search replies.

    Args:
        results: List of classified_saves row dicts.

    Returns:
        Markdown-formatted string, or a 'nothing here' message if empty.
    """
    if not results:
        return "Nothing saved yet."

    lines = []
    for r in results:
        domain = r.get("domain")
        source = f"_{r.get('category', '?')} · {domain}_" if domain else f"_{r.get('category', '?')}_"
        insight = r.get("key_insight", "")
        lines.append(f"*{r['title']}*\n{source}\n{insight}")

    return "\n\n".join(lines)
