# Telegram Dashboard Agent (User Account)

Server-rendered web dashboard that logs into **your Telegram user account** (via Telethon session), periodically fetches recent messages from selected chats, asks OpenAI for a natural reply suggestion (same language/style), and shows a simple “Send / Decline” workflow in a browser.

## What you get

- **/chats**: sync dialogs from Telegram + select which chats to monitor
- **/**: suggestions list (pending first), each with:
  - chat title
  - suggested reply (original language)
  - Russian translation
  - Send / Decline buttons
- **/settings**: configure polling + limits
- **Prompts as JSON** in `./prompts` (editable + reloadable)
- **SQLite** persistence in `./data/app.db`
- **Telethon session** stored locally in `./data/telethon.session`

## Requirements

- Python 3.11+ recommended
- A Telegram “app” (API ID + API HASH) from `https://my.telegram.org/apps`
- OpenAI API key

## Setup

### 1) Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Configure environment variables

Copy the example file and fill it:

```bash
cp .env.example .env
```

Required:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `OPENAI_API_KEY`

Optional:

- `TELEGRAM_PHONE` (used by the login helper script)
- `OPENAI_MODEL` (default: `gpt-4o-mini`)

### 3) Login to Telegram (one-time)

This app uses Telethon **user session** auth (not a bot token). Do this once on the server:

```bash
python scripts/telegram_login.py
```

It will prompt for your phone and the Telegram login code (and 2FA password if enabled). After success you should have:

- `./data/telethon.session`

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open in your browser:

- `http://YOUR_SERVER_IP:8000/`

## How it works (high level)

- You select chats to monitor in **/chats** (stored in SQLite `chats.is_selected`)
- A lightweight asyncio scheduler runs inside FastAPI:
  - every **N minutes** it fetches last **K messages** for each selected chat
  - skips chats that already have enough **pending** suggestions (configurable)
  - skips if messages didn’t change since last successful run
  - asks OpenAI for JSON output: `suggested_text` + `ru_translation`
  - stores a `suggestions` row with status `pending`
- In **/** you can Send / Decline:
  - **Send** posts the suggested message back into the correct Telegram chat
  - **Decline** dismisses the suggestion

## Prompts (JSON in repo)

Prompt files live in:

- `./prompts/system.json`
- `./prompts/suggest_reply.json`

They are loaded by `app/prompts.py` (`PromptStore`) and can be reloaded from **/settings** with “Reload prompts”.

## Notes / safety

- Secrets are loaded from `.env` (never committed).
- Telegram + OpenAI failures are logged; OpenAI/Telegram issues shouldn’t crash the web server.
- The app intentionally avoids extra infrastructure (no Celery, no React, no microservices).


