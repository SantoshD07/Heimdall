# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Heimdall** ("Second Brain") is a Telegram bot + multi-agent AI backend for personal knowledge management. Users send URLs, screenshots, and notes to a Telegram bot; the system extracts and classifies the content asynchronously, then replies with a structured summary.

The authoritative implementation spec is `heimdall_revised_impl_plan.md`. All code scaffolds, database schema, and phased rollout details are there.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Entry point | Telegram bot (`python-telegram-bot 20.x`) |
| Backend API | FastAPI + Uvicorn |
| Task queue | Celery + Redis |
| AI pipeline | Google ADK multi-agent with Gemini 2.0 Flash |
| URL extraction | Trafilatura |
| Image OCR | Google Vision API |
| Database | Supabase (PostgreSQL + pgvector) |
| Monitoring | Celery Flower |
| Hosting | Railway.app |

---

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run FastAPI backend locally
uvicorn main:app --reload --port 8000

# Run Celery worker
celery -A celery_app worker --loglevel=info --concurrency=4

# Run Celery beat scheduler
celery -A celery_app beat --loglevel=info

# Run Flower monitoring UI
celery -A celery_app flower --port=5555
```

Production services run from `Procfile` on Railway — one service each for `web`, `worker`, `beat`, and `flower`.

---

## Architecture

### Data Flow

```
User → Telegram bot → handle_message()
  → insert_raw() [raw_saves, status=pending]  ← data safe before processing
  → process_save.delay()                       ← Celery/Redis async
  → Orchestrator Agent
      url        → Fetcher Agent (Trafilatura)
      screenshot → Vision Agent (Google Vision API)
      note       → Note Agent (validate/clean)
  → Classifier Agent → write_classified()
  → insert_classified() [classified_saves, status=done]
  → _send_telegram() confirmation to user
```

### Two-Table Pattern

- `raw_saves` — immutable inbox. Written instantly before any processing. Never modified except `status` and `error_msg`.
- `classified_saves` — enriched knowledge. Written only on successful pipeline completion. References `raw_saves.id`.

### Agent Boundaries

Each agent does exactly one thing:
- **Orchestrator** — routes by content type, coordinates sub-agents, never extracts or classifies
- **Fetcher / Vision / Note** — extract text only, return verbatim, never summarize
- **Classifier** — structures extracted text into `title`, `summary`, `key_insight`, `category`, `tags`

Agents are defined with `google.adk.agents.LlmAgent` and call typed Python functions via `FunctionTool`.

### Key Design Decisions

- **Celery owns all heavy work.** URL fetching, OCR, ADK calls — nothing runs inside the FastAPI request cycle.
- **Retries are automatic.** `process_save` retries up to 3× with exponential backoff. A separate beat task re-enqueues `failed` saves still under the retry limit every 5 minutes.
- **Sending Telegram messages from workers** uses direct `httpx` HTTP calls (`_send_telegram`), not the bot event loop — the worker has no event loop.

### Project Structure (target)

```
brain-bot/
├── main.py                   # FastAPI app + webhook registration
├── celery_app.py             # Celery instance, config, beat schedule
├── bot/
│   ├── handlers.py           # Telegram update handlers
│   └── replies.py            # Message formatters (fmt_save, fmt_list)
├── pipeline/
│   ├── tasks.py              # process_save + retry_failed Celery tasks
│   └── agents/
│       ├── __init__.py       # run_pipeline() entry point
│       ├── orchestrator.py   # Routes to sub-agents
│       ├── fetcher.py        # URL → text via Trafilatura
│       ├── vision.py         # Image → text via Google Vision
│       ├── note.py           # Text clean/validate
│       └── classifier.py     # Text → structured save + _results store
├── storage/
│   └── db.py                 # All Supabase reads/writes
└── scheduler/
    └── digest.py             # Weekly digest beat task + build_digest()
```

### Environment Variables

```
BOT_TOKEN             Telegram bot token
WEBHOOK_URL           Public URL for /webhook endpoint
SUPABASE_URL          Supabase project URL
SUPABASE_SERVICE_KEY  Supabase service role key
REDIS_URL             Redis connection string
ADK_API_KEY           Google ADK / generativeai API key
VISION_API_KEY        Google Vision API key
```

---

## Upgrade Roadmap

```
v1.0   Telegram bot → Celery/Redis → multi-agent ADK → Supabase (MVP)
v1.1   /digest command + Celery beat weekly digest
v1.2   pgvector embeddings → semantic search
v2.0   PWA (Next.js/Vercel) browse UI — same backend
v3.0   iOS native app with share extension — same backend
v3.1   PDF agent, YouTube transcript agent (plug into orchestrator)
```
