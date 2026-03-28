from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

logger = logging.getLogger(__name__)


async def start(update: Update, _ctx) -> None:
    await update.message.reply_text(
        "Hi! I'm Heimdall — your second brain.\n\n"
        "Send me a URL, a screenshot, or a note and I'll save and classify it for you."
    )


async def handle_message(update: Update, _ctx) -> None:
    message = update.message
    if not message:
        return

    if message.text:
        content_type = "note"
        preview = message.text[:80]
    elif message.photo:
        content_type = "screenshot"
        preview = "<image>"
    elif message.document:
        content_type = "document"
        preview = message.document.file_name or "<file>"
    else:
        await message.reply_text("I can handle text, URLs, and images for now.")
        return

    logger.info("Received %s from user %s", content_type, message.from_user.id)
    await message.reply_text(f"Got it! Saving your {content_type}: {preview}\n\n(Processing pipeline not wired yet.)")


def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    return app
