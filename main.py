from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from telegram import Update

from bot.handlers import build_application

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]

app = FastAPI(title="Heimdall")
_tg_app = build_application(BOT_TOKEN)


@app.on_event("startup")
async def startup() -> None:
    await _tg_app.initialize()
    webhook_endpoint = f"{WEBHOOK_URL}/webhook"
    await _tg_app.bot.set_webhook(webhook_endpoint)
    logger.info("Webhook registered: %s", webhook_endpoint)


@app.on_event("shutdown")
async def shutdown() -> None:
    await _tg_app.shutdown()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    data = await request.json()
    update = Update.de_json(data, _tg_app.bot)
    await _tg_app.process_update(update)
    return Response(status_code=200)
