# Full Implementation Plan
## Second Brain — Telegram Bot + Multi-Agent Google ADK + Supabase + Redis/Celery

---

## Stack at a glance

| Layer | Technology | Why |
|-------|-----------|-----|
| Entry point | Telegram bot | No app, no expiry, works on any device |
| Backend API | Python 3.11 + FastAPI | Webhook receiver, thin orchestration layer |
| Task queue | Celery + Redis | Persistent jobs, retries, worker visibility |
| AI pipeline | Google ADK multi-agent | Orchestrator routes to specialist sub-agents |
| Extraction — URL | Fetcher agent + Trafilatura | Dedicated agent, clean article text |
| Extraction — image | Vision agent + Google Vision API | Dedicated agent, OCR to text |
| Extraction — note | Note agent | Validate + clean plain text |
| Classification | Classifier agent + Gemini 2.0 Flash | Decoupled from extraction entirely |
| Database | Supabase — Postgres + pgvector | Two-table inbox/processed pattern |
| Monitoring | Celery Flower | Task visibility, manual retries |
| Hosting | Railway | Web + worker + beat + Redis as separate services |
| Browse UI (Phase 2) | PWA — Next.js + Vercel | Same backend |
| Mobile (Phase 3) | iOS SwiftUI + share extension | Same backend |

---

## Design principles

1. **Telegram is the UI for v1.** No app, no distribution, no expiry.
2. **Never lose a save.** Write to `raw_saves` instantly before anything else touches the content.
3. **Two-table separation.** `raw_saves` = raw inbox, append-only. `classified_saves` = enriched knowledge, written only on success.
4. **Celery owns all heavy work.** URL fetching, OCR, ADK calls — nothing runs in the FastAPI request cycle.
5. **Each agent does one thing.** Orchestrator routes. Sub-agents extract. Classifier classifies. No agent crosses boundaries.
6. **One backend, multiple frontends.** PWA and iOS app plug into the same FastAPI + Supabase core later.

---

## Multi-agent pipeline design

```
Celery task
    │
    ▼
Orchestrator Agent        ← inspects type, routes, coordinates
    │
    ├─── url ──────► Fetcher Agent   (tool: fetch_url via Trafilatura)
    ├─── screenshot ► Vision Agent   (tool: ocr_image via Google Vision API)
    └─── note ──────► Note Agent     (tool: validate_text, clean + passthrough)
    │
    │   ◄── extracted text returned to orchestrator
    │
    ▼
Classifier Agent          ← receives clean text, produces structured output
    │                        (tool: write_classified)
    ▼
classified_saves (Supabase)
```

**Why this structure:**
- The classifier never needs to know how content was acquired
- Each extraction agent can evolve independently — swap Trafilatura for a different scraper without touching classification
- Failures are isolated — a Vision API outage only affects screenshots, not URLs or notes
- Easy to add new content types later (PDF agent, YouTube transcript agent) without changing the classifier

---

## Project structure

```
brain-bot/
├── main.py                   # FastAPI app + webhook registration
├── celery_app.py             # Celery instance + config + beat schedule
├── bot/
│   ├── handlers.py           # Telegram update handlers
│   └── replies.py            # Message formatters
├── pipeline/
│   ├── tasks.py              # Celery task: process_save + retry_failed
│   └── agents/
│       ├── __init__.py       # Exports: run_pipeline
│       ├── orchestrator.py   # Orchestrator agent + entry point
│       ├── fetcher.py        # Fetcher sub-agent (URL → text)
│       ├── vision.py         # Vision sub-agent (image → text via OCR)
│       ├── note.py           # Note sub-agent (text → clean text)
│       └── classifier.py     # Classifier agent (text → structured save)
├── storage/
│   └── db.py                 # All Supabase reads/writes
├── scheduler/
│   └── digest.py             # Weekly digest beat task
├── requirements.txt
├── Procfile
└── .env
```

`requirements.txt`:
```
fastapi
uvicorn
python-telegram-bot==20.*
celery[redis]
redis
supabase
google-generativeai
google-adk
trafilatura
httpx
python-dotenv
flower
```

`Procfile`:
```
web:    uvicorn main:app --host 0.0.0.0 --port $PORT
worker: celery -A celery_app worker --loglevel=info --concurrency=4
beat:   celery -A celery_app beat --loglevel=info
flower: celery -A celery_app flower --port=5555
```

`.env`:
```
BOT_TOKEN=
WEBHOOK_URL=
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
REDIS_URL=
ADK_API_KEY=
VISION_API_KEY=
```

---

## Phase 0 — Setup (Day 1)

### 1. Telegram bot
- Message `@BotFather` → `/newbot` → copy `BOT_TOKEN`
- Set commands:
```
search - Search your saves
digest - Get this week's digest
list   - List saves by category
recent - Show last 5 saves
help   - Show all commands
```

### 2. Supabase project
Create project at supabase.com, run this SQL:

```sql
create extension if not exists vector;

-- Table 1: raw inbox — persisted instantly, never modified except status
create table raw_saves (
  id             text primary key,
  user_id        text not null,
  saved_at       timestamptz default now(),
  type           text check (type in ('url','screenshot','note')),
  raw_content    text not null,
  status         text default 'pending'
                 check (status in ('pending','processing','done','failed')),
  retry_count    int default 0,
  error_msg      text,
  celery_task_id text
);

-- Table 2: classified knowledge — written only on successful classification
create table classified_saves (
  id             text primary key references raw_saves(id),
  user_id        text not null,
  classified_at  timestamptz default now(),
  title          text,
  domain         text,
  summary        text,
  key_insight    text,
  category       text,
  tags           text[],
  full_text      text,
  embedding      vector(1536)     -- populated in Phase 2
);

-- Indexes
create index on raw_saves (user_id, status, saved_at desc);
create index on raw_saves (status) where status = 'pending';
create index on classified_saves (user_id, category);
create index on classified_saves (user_id, classified_at desc);
create index on classified_saves using gin(tags);
create index on classified_saves using gin(
  to_tsvector('english',
    coalesce(title,'') || ' ' ||
    coalesce(summary,'') || ' ' ||
    coalesce(key_insight,''))
);
```

### 3. Railway setup
- Create project → add four services: `web`, `worker`, `beat`, `flower`
- Add Redis plugin (auto-sets `REDIS_URL`)
- Set all env vars across services

---

## Phase 1 — Celery + Redis (Day 1)

### celery_app.py
```python
from celery import Celery
import os

celery = Celery(
    "brain",
    broker=os.environ["REDIS_URL"],
    backend=os.environ["REDIS_URL"],
    include=["pipeline.tasks", "scheduler.digest"]
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    task_annotations={
        "pipeline.tasks.process_save": {
            "max_retries": 3,
            "default_retry_delay": 30,
        }
    },
    beat_schedule={
        "weekly-digest": {
            "task": "scheduler.digest.send_weekly_digests",
            "schedule": 604800,
            "options": {"expires": 3600}
        },
        "retry-failed-saves": {
            "task": "pipeline.tasks.retry_failed",
            "schedule": 300,
        }
    }
)
```

---

## Phase 2 — FastAPI + bot handlers (Days 2–3)

### main.py
```python
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters
from bot.handlers import (handle_message, handle_search,
                          handle_recent, handle_list, handle_digest)
import os

app = FastAPI()
bot_app = Application.builder().token(os.environ["BOT_TOKEN"]).build()

bot_app.add_handler(
    MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
bot_app.add_handler(CommandHandler("search", handle_search))
bot_app.add_handler(CommandHandler("recent", handle_recent))
bot_app.add_handler(CommandHandler("list",   handle_list))
bot_app.add_handler(CommandHandler("digest", handle_digest))

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}

@app.on_event("startup")
async def startup():
    await bot_app.initialize()
    await bot_app.bot.set_webhook(
        url=f"{os.environ['WEBHOOK_URL']}/webhook"
    )
```

### bot/handlers.py
```python
import re, uuid
from telegram import Update
from telegram.ext import ContextTypes
from storage.db import (insert_raw, update_raw,
                         get_recent, search_saves, get_by_category)
from pipeline.tasks import process_save
from bot.replies import fmt_list
from scheduler.digest import build_digest

URL_RE = re.compile(r'https?://\S+')

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = msg.text or msg.caption or ""
    uid = str(msg.from_user.id)
    save_id = str(uuid.uuid4())[:8]

    if msg.photo:
        file = await ctx.bot.get_file(msg.photo[-1].file_id)
        raw_content = file.file_path
        save_type = "screenshot"
    elif urls := URL_RE.findall(text):
        raw_content = urls[0]
        save_type = "url"
    elif text.strip():
        raw_content = text.strip()
        save_type = "note"
    else:
        return

    # 1. Persist immediately — data is safe before any processing
    insert_raw({
        "id": save_id,
        "user_id": uid,
        "type": save_type,
        "raw_content": raw_content,
        "status": "pending"
    })

    # 2. Enqueue Celery task — store task ID for traceability
    task = process_save.delay(save_id, uid)
    update_raw(save_id, {"celery_task_id": task.id})

    # 3. Instant acknowledgement
    await msg.reply_text(
        "Got it — classifying in the background. "
        "I'll send you the result in a few seconds."
    )

async def handle_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = " ".join(ctx.args).strip()
    if not q:
        await update.message.reply_text("Usage: /search your query")
        return
    results = search_saves(q, uid=str(update.message.from_user.id))
    await update.message.reply_text(
        fmt_list(results) if results else "Nothing found.",
        parse_mode="Markdown"
    )

async def handle_recent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    results = get_recent(uid=str(update.message.from_user.id), n=5)
    await update.message.reply_text(fmt_list(results), parse_mode="Markdown")

async def handle_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cat = " ".join(ctx.args).strip()
    if not cat:
        await update.message.reply_text("Usage: /list Health")
        return
    results = get_by_category(cat, uid=str(update.message.from_user.id))
    await update.message.reply_text(
        fmt_list(results) if results else f"No saves in {cat}.",
        parse_mode="Markdown"
    )

async def handle_digest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Building your digest...")
    text = build_digest(uid=str(update.message.from_user.id))
    await update.message.reply_text(text, parse_mode="Markdown")
```

### bot/replies.py
```python
def fmt_save(r: dict) -> str:
    tags = " ".join(f"#{t}" for t in (r.get("tags") or []))
    domain = r.get("domain") or "note"
    return (
        f"Saved\n\n"
        f"*{r['title']}*\n"
        f"_{r['category']} · {domain}_\n\n"
        f"{r['key_insight']}\n\n"
        f"{tags}"
    )

def fmt_list(results: list) -> str:
    if not results:
        return "Nothing here yet."
    lines = [
        f"*{r['title']}*\n"
        f"_{r.get('category','?')} · {r.get('domain') or 'note'}_\n"
        f"{r.get('key_insight','')}"
        for r in results
    ]
    return "\n\n".join(lines)
```

---

## Phase 3 — Multi-agent pipeline (Days 3–6)

### pipeline/agents/fetcher.py
```python
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
import trafilatura
from urllib.parse import urlparse

def fetch_url(url: str) -> dict:
    """Fetch and extract clean text from a URL using Trafilatura."""
    downloaded = trafilatura.fetch_url(url)
    text = trafilatura.extract(downloaded) or ""
    domain = urlparse(url).netloc.replace("www.", "")
    return {"text": text[:5000], "domain": domain}

fetcher_agent = LlmAgent(
    model="gemini-2.0-flash",
    name="fetcher_agent",
    instruction="""
You are a URL content fetcher.
Given a URL, call fetch_url and return the result as-is.
If the fetch fails or returns empty text, return {"text": "", "domain": ""}.
Do not summarise or modify the text — return it verbatim.
""",
    tools=[FunctionTool(fetch_url)]
)
```

### pipeline/agents/vision.py
```python
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
import httpx, base64, os

def ocr_image(file_url: str) -> dict:
    """Download image from Telegram CDN and extract text via Google Vision."""
    resp = httpx.get(file_url)
    b64 = base64.b64encode(resp.content).decode()
    payload = {"requests": [{
        "image": {"content": b64},
        "features": [{"type": "TEXT_DETECTION"}]
    }]}
    r = httpx.post(
        f"https://vision.googleapis.com/v1/images:annotate"
        f"?key={os.environ['VISION_API_KEY']}",
        json=payload
    )
    anns = r.json()["responses"][0].get("textAnnotations", [])
    return {"text": anns[0]["description"] if anns else ""}

vision_agent = LlmAgent(
    model="gemini-2.0-flash",
    name="vision_agent",
    instruction="""
You are an image OCR agent.
Given a Telegram CDN file URL, call ocr_image to extract text.
Return the result as-is.
If no text is detected, return {"text": ""}.
Do not interpret or summarise — return raw extracted text only.
""",
    tools=[FunctionTool(ocr_image)]
)
```

### pipeline/agents/note.py
```python
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

def validate_text(text: str) -> dict:
    """Clean and validate plain text note input."""
    cleaned = " ".join(text.split())  # normalise whitespace
    return {"text": cleaned[:5000]}

note_agent = LlmAgent(
    model="gemini-2.0-flash",
    name="note_agent",
    instruction="""
You are a note validator.
Given plain text, call validate_text to clean and normalise it.
Return the result as-is. Do not summarise or modify content.
""",
    tools=[FunctionTool(validate_text)]
)
```

### pipeline/agents/classifier.py
```python
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

CATEGORIES = ["Tech", "Health", "Finance", "Science", "Productivity",
              "Design", "Culture", "Society", "Food", "Travel", "Other"]

# Shared result store — keyed by save_id
_results: dict = {}

def write_classified(row: dict) -> dict:
    """Tool called by classifier to persist structured output."""
    _results[row["id"]] = row
    return {"status": "ok"}

classifier_agent = LlmAgent(
    model="gemini-2.0-flash",
    name="classifier_agent",
    instruction=f"""
You are a personal knowledge classifier.
Given clean extracted text and save metadata, call write_classified
with a fully structured row.

Required fields:
- id: the save_id from context
- user_id: the user_id from context
- type: the save type from context
- domain: domain if URL, else null
- title: ≤10 words, descriptive
- summary: exactly 2 sentences, factual
- key_insight: the single most useful takeaway, 1 sentence
- category: exactly one from {CATEGORIES}
- tags: list of 2–4 lowercase strings, no spaces
- full_text: first 500 chars of extracted text

Always call write_classified. Never return plain text.
""",
    tools=[FunctionTool(write_classified)]
)
```

### pipeline/agents/orchestrator.py
```python
from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool
from pipeline.agents.fetcher import fetcher_agent
from pipeline.agents.vision import vision_agent
from pipeline.agents.note import note_agent
from pipeline.agents.classifier import classifier_agent, _results

orchestrator = LlmAgent(
    model="gemini-2.0-flash",
    name="orchestrator",
    instruction="""
You coordinate the full content extraction and classification pipeline.

Step 1 — Route based on save type:
  - type = 'url'        → call fetcher_agent with the raw_content URL
  - type = 'screenshot' → call vision_agent with the raw_content file URL
  - type = 'note'       → call note_agent with the raw_content text

Step 2 — Take the extracted text from the sub-agent result and pass it
  along with save_id, user_id, type, and domain to classifier_agent.

Always complete both steps. Never skip classification.
""",
    sub_agents=[fetcher_agent, vision_agent, note_agent, classifier_agent]
)
```

### pipeline/agents/\_\_init\_\_.py
```python
from pipeline.agents.orchestrator import orchestrator
from pipeline.agents.classifier import _results

async def run_pipeline(save_id: str, user_id: str,
                       save_type: str, raw_content: str) -> dict:
    """Entry point called by the Celery task."""
    _results.pop(save_id, None)

    prompt = (
        f"save_id={save_id}\n"
        f"user_id={user_id}\n"
        f"type={save_type}\n"
        f"raw_content={raw_content}\n\n"
        f"Process this save end to end."
    )
    await orchestrator.run_async(prompt)

    result = _results.get(save_id)
    if not result:
        raise RuntimeError(
            f"Pipeline produced no classified output for save {save_id}"
        )
    return result
```

---

## Phase 4 — Celery task (Day 4)

### pipeline/tasks.py
```python
from celery_app import celery
from celery.utils.log import get_task_logger
from pipeline.agents import run_pipeline
from storage.db import (get_raw_by_id, insert_classified,
                         update_raw, get_failed_saves)
from bot.replies import fmt_save
import asyncio, httpx, os

logger = get_task_logger(__name__)

@celery.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="pipeline.tasks.process_save"
)
def process_save(self, save_id: str, user_id: str):
    logger.info(f"Starting pipeline for save {save_id}")
    update_raw(save_id, {"status": "processing"})

    try:
        raw = get_raw_by_id(save_id)
        if not raw:
            raise ValueError(f"raw_save {save_id} not found in DB")

        # Run the full multi-agent pipeline (sync wrapper for Celery)
        result = asyncio.get_event_loop().run_until_complete(
            run_pipeline(
                save_id=save_id,
                user_id=user_id,
                save_type=raw["type"],
                raw_content=raw["raw_content"]
            )
        )

        # Write classified result and mark raw as done
        insert_classified(result)
        update_raw(save_id, {"status": "done"})

        # Send confirmation to user via Telegram Bot API directly
        _send_telegram(user_id, fmt_save(result))
        logger.info(f"Save {save_id} → {result['category']}")

    except Exception as exc:
        logger.error(f"Save {save_id} failed: {exc}")
        raw = get_raw_by_id(save_id) or {}
        update_raw(save_id, {
            "status": "failed",
            "error_msg": str(exc),
            "retry_count": (raw.get("retry_count") or 0) + 1
        })
        raise self.retry(exc=exc,
                         countdown=30 * (self.request.retries + 1))


@celery.task(name="pipeline.tasks.retry_failed")
def retry_failed():
    """Beat task every 5 min — re-enqueue failed saves under retry limit."""
    failed = get_failed_saves(max_retries=3)
    for raw in failed:
        logger.info(f"Re-enqueuing failed save {raw['id']}")
        process_save.delay(raw["id"], raw["user_id"])


def _send_telegram(user_id: str, text: str):
    """Direct HTTP call — no bot event loop needed in worker process."""
    httpx.post(
        f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}/sendMessage",
        json={"chat_id": user_id, "text": text, "parse_mode": "Markdown"}
    )
```

---

## Phase 5 — Supabase storage layer (Days 3–4, parallel)

### storage/db.py
```python
from supabase import create_client
from datetime import datetime, timedelta, timezone
import os

_sb = None

def sb():
    global _sb
    if not _sb:
        _sb = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"]
        )
    return _sb

# ── raw_saves ─────────────────────────────────────────────

def insert_raw(row: dict):
    sb().table("raw_saves").insert(row).execute()

def get_raw_by_id(save_id: str) -> dict | None:
    res = (sb().table("raw_saves")
           .select("*").eq("id", save_id).limit(1).execute())
    return res.data[0] if res.data else None

def update_raw(save_id: str, fields: dict):
    sb().table("raw_saves").update(fields).eq("id", save_id).execute()

def get_failed_saves(max_retries: int = 3) -> list:
    return (sb().table("raw_saves")
            .select("*")
            .eq("status", "failed")
            .lt("retry_count", max_retries)
            .execute().data)

def get_stuck_saves(minutes: int = 10) -> list:
    cutoff = (datetime.now(timezone.utc)
              - timedelta(minutes=minutes)).isoformat()
    return (sb().table("raw_saves")
            .select("*")
            .eq("status", "processing")
            .lt("saved_at", cutoff)
            .execute().data)

# ── classified_saves ──────────────────────────────────────

def insert_classified(row: dict):
    sb().table("classified_saves").insert(row).execute()

def get_recent(uid: str, n: int = 5) -> list:
    return (sb().table("classified_saves")
            .select("*")
            .eq("user_id", uid)
            .order("classified_at", desc=True)
            .limit(n)
            .execute().data)

def get_by_category(category: str, uid: str) -> list:
    return (sb().table("classified_saves")
            .select("*")
            .eq("user_id", uid)
            .ilike("category", category)
            .order("classified_at", desc=True)
            .execute().data)

def search_saves(query: str, uid: str, limit: int = 5) -> list:
    return (sb().table("classified_saves")
            .select("*")
            .eq("user_id", uid)
            .text_search(
                "title,summary,key_insight",
                query,
                config="english"
            )
            .limit(limit)
            .execute().data)

def get_since(uid: str, days: int = 7) -> list:
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=days)).isoformat()
    return (sb().table("classified_saves")
            .select("*")
            .eq("user_id", uid)
            .gte("classified_at", cutoff)
            .order("classified_at", desc=True)
            .execute().data)
```

---

## Phase 6 — Weekly digest beat task (Week 2)

### scheduler/digest.py
```python
from celery_app import celery
from google.adk.agents import LlmAgent
from storage.db import get_since, sb
import json, asyncio, httpx, os

_digest_agent = LlmAgent(
    model="gemini-2.0-flash",
    name="digest_agent",
    instruction="""
You receive a JSON list of saves from the past 7 days.
Write a digest formatted for Telegram Markdown:

1. Find 2–3 topic clusters → *bold cluster heading*
2. Under each: bullet saves with title + one connecting sentence
3. End with "This week's key insight:" — best single takeaway

Under 300 words. Direct, no filler phrases.
"""
)

def build_digest(uid: str) -> str:
    saves = get_since(uid, days=7)
    if not saves:
        return "No saves this week — send me some links to get started."
    response = asyncio.get_event_loop().run_until_complete(
        _digest_agent.run_async(json.dumps(saves, default=str))
    )
    return f"*Your weekly digest*\n\n{response.text}"

@celery.task(name="scheduler.digest.send_weekly_digests")
def send_weekly_digests():
    """Beat task: Sunday 9am UTC — send digest to all active users."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    users = (sb().table("classified_saves")
             .select("user_id")
             .gte("classified_at", cutoff)
             .execute().data)
    seen = set()
    for row in users:
        uid = row["user_id"]
        if uid in seen:
            continue
        seen.add(uid)
        text = build_digest(uid)
        httpx.post(
            f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}/sendMessage",
            json={"chat_id": uid, "text": text, "parse_mode": "Markdown"}
        )
```

---

## Phase 7 — Semantic search with pgvector (Week 3+)

No schema migration needed — `embedding vector(1536)` already exists.

### Generate embedding after classification (add to tasks.py):
```python
import google.generativeai as genai

def store_embedding(save_id: str, text: str):
    genai.configure(api_key=os.environ["ADK_API_KEY"])
    result = genai.embed_content(
        model="models/embedding-001",
        content=text,
        task_type="retrieval_document"
    )
    sb().table("classified_saves").update(
        {"embedding": result["embedding"]}
    ).eq("id", save_id).execute()
```

### SQL similarity function (run once in Supabase):
```sql
create or replace function match_saves(
  query_embedding vector(1536),
  match_uid       text,
  match_count     int default 5
)
returns table (
  id text, title text, key_insight text,
  category text, domain text, similarity float
)
language sql stable as $$
  select id, title, key_insight, category, domain,
         1 - (embedding <=> query_embedding) as similarity
  from classified_saves
  where user_id = match_uid
    and embedding is not null
  order by embedding <=> query_embedding
  limit match_count;
$$;
```

---

## Critical path to working MVP

| Step | What | Est. time |
|------|------|-----------|
| 1 | BotFather → `BOT_TOKEN` | 5 min |
| 2 | Supabase project + schema SQL | 15 min |
| 3 | Railway: web + worker + beat + Redis | 30 min |
| 4 | `handle_message` → `insert_raw` → `process_save.delay()` | 1 hr |
| 5 | Fetcher + Vision + Note sub-agents working independently | 2 hrs |
| 6 | Classifier agent calling `write_classified` correctly | 1 hr |
| 7 | Orchestrator routing and coordinating all agents | 1 hr |
| 8 | Celery task wiring it all together + `_send_telegram` | 1 hr |
| 9 | `/search`, `/recent`, `/list` commands | 1 hr |

Total: solid working product in ~2 days.

---

## Upgrade roadmap

```
v1.0   Telegram bot → Celery/Redis → multi-agent ADK → Supabase
v1.1   + /digest command + Celery beat weekly digest
v1.2   + pgvector embeddings → semantic search
v2.0   + PWA (Next.js/Vercel) — browse, search, digest UI
v2.1   + Supabase Auth — user accounts for PWA
v3.0   + iOS native app with share extension → same backend
v3.1   + PDF agent, YouTube transcript agent (plug into orchestrator)
```
