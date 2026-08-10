# PaperlessAgent

Local-first document agent: drop scanned PDFs and photos into an inbox, and it recovers the text with AI vision OCR, extracts structured metadata, proposes a meaningful filename, and files everything into your folder structure — with you in the loop before anything is written. Metadata lands in SQLite, content is indexed for RAG so you can ask questions about your archive in natural language.

Built with [Google ADK](https://adk.dev/) (Python) + FastAPI. Works with **OpenAI (ChatGPT OAuth or API key)**, **Gemini**, or **Ollama (fully local)**. Everything runs and stays on your machine.

![Inbox with live pipeline](docs/screenshots/inbox.png)

## Features

- **Ingest pipeline**: Read → AI OCR → Extract → Name → Review → File → Index, with a live workflow visualization while it runs
- **Human-in-the-loop review**: every proposed filing waits in a review queue where you can correct filename, category, date, amount, and summary before approving — nothing touches your filesystem until you say so (optional; can be switched to fully automatic)
- **Duplicate detection**: SHA-256 file checksums, normalized content hashes, and text-similarity matching flag re-scans and near-duplicates before they are filed
- **Ask your archive**: RAG-backed natural-language questions with source citations
- **Local storage**: configurable inbox and per-category archive folders, `data/paperless.db` (SQLite), `data/chroma/` (vectors)
- **Inbox polling**: automatic processing of new scans on a configurable interval
- **Web app**: single-page UI with four theme presets (dark/light), toast notifications, and a mockup mode for clean screenshots
- **Self-update**: check and install new releases from GitHub directly from Settings

## Screenshots

All screenshots use the built-in mockup mode (Settings → Look & feel), which fills the UI with demo data.

| Review queue | Archive (Slate theme) |
| --- | --- |
| ![Review](docs/screenshots/review.png) | ![Archive](docs/screenshots/archive.png) |

| Ask the archive | Settings |
| --- | --- |
| ![Ask](docs/screenshots/ask.png) | ![Settings](docs/screenshots/settings.png) |

## Install

One command — clones into `~/paperlessagent`, creates a venv, installs dependencies, and writes a starter `.env`:

```bash
curl -fsSL https://raw.githubusercontent.com/dpastoetter/paperlessagent/main/scripts/install.sh | bash
```

Then start it:

```bash
cd ~/paperlessagent
source .venv/bin/activate
uvicorn app.main:app --port 8080
```

Open [http://localhost:8080](http://localhost:8080). Sign in under **Settings → Authentication**, point the inbox at your scan folder, and process a document.

Optional:

```bash
# Install somewhere else
PAPERLESS_DIR=~/apps/paperlessagent curl -fsSL \
  https://raw.githubusercontent.com/dpastoetter/paperlessagent/main/scripts/install.sh | bash

# Re-run anytime to pull the latest code and refresh dependencies
curl -fsSL https://raw.githubusercontent.com/dpastoetter/paperlessagent/main/scripts/install.sh | bash
```

Needs **Python 3.10+**, **git**, and **Poppler** (`pdftoppm`) for PDF OCR:

```bash
# Fedora / RHEL
sudo dnf install poppler-utils
# Debian / Ubuntu
sudo apt install poppler-utils
# macOS
brew install poppler
```

### Manual setup

```bash
git clone https://github.com/dpastoetter/paperlessagent.git
cd paperlessagent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### OpenAI / Codex auth

In the web UI, open **Settings → Authentication** and choose one:

1. **Sign in with ChatGPT** — Codex-compatible OAuth (PKCE). Tokens are stored in `~/.codex/auth.json` and model calls use the Codex Responses backend against your ChatGPT subscription.
2. **Save API key** — stores a Platform `sk-…` key in the same auth file (usage-based billing).

You can also set `OPENAI_API_KEY` in `.env`. Auth resolution prefers an API key when present, otherwise ChatGPT OAuth tokens.

```bash
PAPERLESS_LLM_PROVIDER=openai
PAPERLESS_MODEL=gpt-5.6-luna
# Optional override for ChatGPT OAuth (defaults to gpt-5.6-luna):
# PAPERLESS_CODEX_MODEL=gpt-5.6-terra
PAPERLESS_EMBEDDING_MODEL=text-embedding-3-small
```

ChatGPT OAuth only supports Codex models (e.g. `gpt-5.6-luna` / `terra` / `sol`). Platform IDs like `gpt-4.1` are rejected — the app auto-falls back to `gpt-5.6-luna` in that case. ChatGPT OAuth uses a local embedding fallback for RAG; for higher-quality embeddings, save an API key.

### Gemini auth (alternative)

```bash
PAPERLESS_LLM_PROVIDER=gemini
GOOGLE_API_KEY=...
PAPERLESS_MODEL=gemini-flash-latest
PAPERLESS_EMBEDDING_MODEL=text-embedding-004
```

### Ollama (alternative, fully local)

Run everything on your own hardware — no cloud account, no API key. The agent, AI vision OCR, and RAG embeddings all go through your local Ollama server. Pick a **multimodal** model, since OCR sends page images to the same model:

```bash
ollama pull gemma3            # or llama3.2-vision, qwen2.5vl, minicpm-v
ollama pull nomic-embed-text  # embeddings for RAG
```

```bash
PAPERLESS_LLM_PROVIDER=ollama
PAPERLESS_MODEL=gemma3
PAPERLESS_EMBEDDING_MODEL=nomic-embed-text
# OLLAMA_BASE_URL=http://localhost:11434   (default)
```

If `PAPERLESS_LLM_PROVIDER` is unset, the app auto-selects OpenAI when an OpenAI/Codex key is available, otherwise Gemini when `GOOGLE_API_KEY` is set. Ollama must be selected explicitly.

## Run the web app

```bash
source .venv/bin/activate
uvicorn app.main:app --port 8080
```

Uvicorn binds to `127.0.0.1` by default — keep it that way. The API has no login; mutating routes are protected against cross-site form posts by a custom header the UI always sends, but binding with `--host 0.0.0.0` would still expose your documents and settings to anyone on the network.

Open [http://localhost:8080](http://localhost:8080).

## Typical flow

1. Upload a scan in **Inbox** (or copy files into your source folder)
2. Click **Process inbox** — or let the poller pick it up automatically
3. Check **Review**: correct the proposal if needed, then **Approve & file** (duplicates are flagged with a match score)
4. Find the filed document in **Archive**, or ask questions in **Ask**

Filing rules (source folder, category → folder mapping, poll interval, review requirement) live in **Settings → Filing & scanning** and are stored in `data/settings.json`.

## Updates

**Settings → Software update** checks the GitHub releases of this repository and can download and install the latest version in place. Your documents, database, settings, and credentials (`data/`, `.env`) are never touched by an update. Publish a release with a version tag (e.g. `v0.2.0`) to make it available to the updater.

## Mockup mode

**Settings → Look & feel → Mockup mode** fills every view with demo data — useful for screenshots and demos without exposing personal documents. It is client-side only: while enabled, no real data is read or written, and all mutating actions are blocked. Also available ad hoc via URL parameters: `http://localhost:8080/?mock=1&theme=slate#/archive`.

## Development

### Tests & pre-commit gate

```bash
pytest -q                 # full suite, offline (embeddings and LLM calls are stubbed)
./scripts/precommit.sh    # syntax checks + secret guard + tests
```

Install the pre-commit hook once per clone so the gate runs on every commit:

```bash
ln -sf ../../scripts/precommit.sh .git/hooks/pre-commit
```

The gate refuses commits containing `.env` files, databases, `data/` content, or anything that looks like an API key.

### ADK agents (debug)

```bash
adk web --port 8000
# or
adk run paperless_agent
adk run query_agent
```

### Inbox watcher (CLI alternative to the built-in poller)

```bash
python scripts/watch_inbox.py --process-existing
```

## Project layout

```
paperless_agent/       # ingest pipeline, review queue, dedup, updater, auth/llm helpers
  ingest.py            #   OCR → extract → name → review gate → file + index
  review.py            #   human-in-the-loop queue (approve / reject)
  dedup.py             #   checksum + content-hash + similarity duplicate detection
  updater.py           #   self-update from GitHub releases
query_agent/           # RAG Q&A agent
app/                   # FastAPI backend + single-page web UI (app/static/)
scripts/               # install.sh, watch_inbox.py, precommit.sh
tests/                 # offline test suite
docs/screenshots/      # README screenshots (generated with mockup mode)
data/                  # created at runtime (gitignored)
```

## Notes

- Text recovery on ingest always uses AI vision OCR on rendered page images; a PDF text layer, when present, serves as a hint and fallback.
- If you switch embedding providers (Gemini / OpenAI / Ollama), re-index documents — vector spaces are not compatible.

## License

[Apache License 2.0](LICENSE) — Copyright 2026 dpastoetter
