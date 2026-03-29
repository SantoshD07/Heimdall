from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from storage.db import insert_raw
from pipeline.tasks import process_save

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://\S+")


async def start(update: Update, _ctx) -> None:
    await update.message.reply_text(
        "Hi! I'm Heimdall — your second brain.\n\n"
        "Send me a URL, a screenshot, or a note and I'll save and classify it for you."
    )


async def handle_message(update: Update, _ctx) -> None:
    message = update.message
    if not message:
        return

    user_id = message.from_user.id

    if message.photo:
        file_id = message.photo[-1].file_id  # highest resolution
        row = insert_raw(user_id=user_id, content_type="screenshot", file_id=file_id)
        process_save.delay(row["id"])
        await message.reply_text("Got it — classifying in the background. I'll send you the result in a few seconds.")

    elif message.text:
        text = message.text
        content_type = "url" if _URL_RE.search(text) else "note"
        row = insert_raw(user_id=user_id, content_type=content_type, raw_content=text)
        process_save.delay(row["id"])
        await message.reply_text("Got it — classifying in the background. I'll send you the result in a few seconds.")

    else:
        await message.reply_text("I can handle text, URLs, and images for now.")
        return

    logger.info("Saved %s for user %s → %s", row["content_type"], user_id, row["id"])


def build_application(token: str) -> Application:
    app = Application.builder().token(token).updater(None).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    return app
