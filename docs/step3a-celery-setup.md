# Step 3a — Celery + Redis Setup

## What was built

A Celery task queue that will handle all heavy processing outside the FastAPI
request cycle.

### Files added / changed

| File | What it does |
|------|-------------|
| `celery_app.py` | Creates the Celery app, connects to Redis, sets serialization and retry config |
| `pipeline/tasks.py` | Defines `process_save` task — branches on content type, logs TODOs for each step |
| `storage/db.py` | Added `get_raw_save()` and `update_raw_status()` helpers used by the task |
| `requirements.txt` | Added `celery==5.4.0`, `redis==5.0.8` |

---

## How it works

```
User sends message
    → handler saves row to raw_saves (status=pending)
    → process_save.delay(raw_id)     ← enqueues task in Redis (Step 3b)
         │
         ▼  (separate worker process)
    process_save(raw_id)
         → fetches row from raw_saves
         → sets status=processing
         → branches:
              url        → TODO fetch text (Step 4)
              screenshot → TODO run OCR   (Step 4)
              note       → TODO passthrough (Step 4)
         → sets status=done
```

### Status transitions in raw_saves

```
pending → processing → done
                     → failed  (after 3 retries with exponential backoff)
```

### Retry strategy

| Attempt | Delay |
|---------|-------|
| Retry 1 | 2s    |
| Retry 2 | 4s    |
| Retry 3 | 8s    |

After 3 failures the row is marked `failed` and `error_msg` is written.

---

## Prerequisites

### Redis (local dev)

```bash
docker run -d -p 6379:6379 redis:7-alpine
```

Or install Redis directly:
- **Windows**: use [Memurai](https://www.memurai.com/) or WSL2 + `apt install redis`
- **Mac**: `brew install redis && brew services start redis`

### Install new dependencies

```bash
pip install -r requirements.txt
```

---

## Running

Three processes must run simultaneously (each in its own terminal):

```bash
# Terminal 1 — FastAPI server
uvicorn main:app --reload --port 8000

# Terminal 2 — Celery worker
celery -A celery_app worker --loglevel=info --concurrency=4

# Terminal 3 — Redis (if not running as a service)
docker run -p 6379:6379 redis:7-alpine
```

---

## What's next

**Step 3b** — call `process_save.delay(raw_id)` from `bot/handlers.py` right
after `insert_raw()` so every incoming message automatically enqueues a task.

**Step 4** — fill in the three content-type branches with real extraction logic.
