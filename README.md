# PaperlessAgent

Local-first document agent: drop scanned PDFs and photos into an inbox, and it recovers the text with AI vision OCR, extracts structured metadata, proposes a meaningful filename, and files everything into your folder structure — with you in the loop before anything is written. Metadata lands in SQLite, content is indexed for RAG so you can ask questions about your archive in natural language.

Built with [Google ADK](https://adk.dev/) (Python) + FastAPI. Works with **OpenAI (ChatGPT OAuth or API key)**, **Gemini**, or **Ollama (fully local)**. Everything runs and stays on your machine.

![Inbox with live pipeline](docs/screenshots/inbox.png)

## Features

- **Ingest pipeline**: Open file → Transcribe → Find details → Name file → Review → Save → Make searchable, with a live workflow strip (Server-Sent Events) and hover descriptions on each step
- **Adaptive OCR**: per-page text-layer vs AI vision (`fast` / `balanced` / `maximum`); cloud providers run vision pages concurrently
- **Per-file cancel & retry**: stop a stuck or slow file mid-pipeline; retry failed or cancelled items from the inbox queue (readable error toasts for API failures)
- **Human-in-the-loop review**: every proposed filing waits in a review queue where you can correct filename, category, date, parties, reference IDs, amount (financial docs only), and summary before approving — nothing touches your filesystem until you say so (optional; can be switched to fully automatic)
- **Smart metadata extraction**: category-aware fields — `subject`, `parties`, and `reference_ids` for all document types; amount/currency only for invoices, receipts, bank/tax/utility/insurance documents. Missing `doc_type` defaults to `other`; common aliases (`document_type`, `category`) are accepted
- **Duplicate detection**: SHA-256 file checksums, normalized content hashes, and text-similarity matching flag re-scans and near-duplicates before they are filed
- **Ask your archive**: grounded RAG + FTS5 keyword search with source citations (no “recent docs” padding when retrieval misses)
- **Local storage**: configurable inbox and per-category archive folders, `data/paperless.db` (SQLite + FTS5), `data/chroma/` (vectors)
- **Inbox polling**: automatic processing of new scans on a configurable interval
- **Local Ollama tooling**: start the daemon, pull models, show CPU/GPU usage for loaded models, unload or restart Ollama from Settings
- **Boot autostart (Linux)**: optional systemd user service so the web UI comes up after login or reboot
- **Web app**: modular ES-module UI with four theme presets (dark/light), toast notifications, and a mockup mode for clean screenshots
- **Self-update**: check and install new releases from GitHub directly from Settings
- **CI & coverage**: `./scripts/ci.sh` runs format, lint, mypy, Vitest, and pytest with a coverage floor
## Screenshots

All screenshots use the built-in mockup mode (Settings → Look & feel), which fills the UI with demo data.

| Review queue | Archive |
| --- | --- |
| ![Review](docs/screenshots/review.png) | ![Archive](docs/screenshots/archive.png) |

| Ask the archive | Settings |
| --- | --- |
| ![Ask](docs/screenshots/ask.png) | ![Settings](docs/screenshots/settings.png) |

All five shots use mockup mode with the Slate theme (`?mock=1&theme=slate`). Regenerate with `node scripts/capture-screenshots.mjs` while the app is running on port 8080.

Product deck (open in a browser): [`docs/deck/index.html`](docs/deck/index.html) — arrow keys or Space to advance.

## Install

Cloning the repo alone is not enough — Python dependencies must be installed into a local virtualenv (`.venv`). Without that step you will see missing activate scripts and/or `ModuleNotFoundError: No module named 'fastapi'` when starting the app (often because a system-wide `uvicorn` is used instead of the venv one).

The recommended installers download the **latest verified GitHub Release** tarball (same artifact as the in-app updater), sync into the install directory (preserving `.env`, `.venv`, `data/`, and `.git`), create `.venv`, install dependencies, and write a starter `.env`.

### Linux

Prerequisites: **Python 3.10+** (with `venv`), **curl**, **tar**, and **Poppler** (`pdftoppm`) for PDF OCR:

```bash
# Fedora / RHEL
sudo dnf install poppler-utils
# Debian / Ubuntu
sudo apt install python3-venv poppler-utils
```

```bash
curl -fsSL https://github.com/dpastoetter/paperlessagent/releases/latest/download/install.sh | bash
```

```bash
cd ~/paperlessagent
source .venv/bin/activate
uvicorn app.main:app --port 8080
```

Optional install location / developer git mode:

```bash
PAPERLESS_DIR=~/apps/paperlessagent curl -fsSL \
  https://github.com/dpastoetter/paperlessagent/releases/latest/download/install.sh | bash

PAPERLESS_INSTALL_SOURCE=git curl -fsSL \
  https://github.com/dpastoetter/paperlessagent/releases/latest/download/install.sh | bash
```

**Uninstall** (app code, `.venv`, local `data/`, `.env`):

```bash
rm -rf "${PAPERLESS_DIR:-$HOME/paperlessagent}"
```

This does not delete ChatGPT/OpenAI credentials in `~/.codex/auth.json` or archive/inbox folders outside the install directory. If you enabled Settings → Autostart, disable it first or run `systemctl --user disable paperlessagent.service` (systemd autostart is Linux-only).

### macOS

Prerequisites via Homebrew:

```bash
brew install python poppler
```

Same installer as Linux:

```bash
curl -fsSL https://github.com/dpastoetter/paperlessagent/releases/latest/download/install.sh | bash
```

```bash
cd ~/paperlessagent
source .venv/bin/activate
uvicorn app.main:app --port 8080
```

**Uninstall:**

```bash
rm -rf "${PAPERLESS_DIR:-$HOME/paperlessagent}"
```

Does not remove `~/.codex/auth.json` or external archive folders. Boot autostart (systemd) is **not available** on macOS — use Settings → Autostart only on Linux.

### Windows

Prerequisites: **Python 3.10+** from [python.org](https://www.python.org/downloads/) (or `winget install Python.Python.3.12`) and **Poppler** so `pdftoppm` is on `PATH` (for example a [poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases) build, or search with `winget search poppler`).

In PowerShell:

```powershell
irm https://github.com/dpastoetter/paperlessagent/releases/latest/download/install.ps1 | iex
```

```powershell
cd $env:USERPROFILE\paperlessagent
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

If `Activate.ps1` is blocked, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Optional: `$env:PAPERLESS_DIR = "$env:USERPROFILE\apps\paperlessagent"` before the installer. WSL can use the Linux `install.sh` flow instead.

**Uninstall:**

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\paperlessagent"
```

Does not remove `%USERPROFILE%\.codex\auth.json` or archive folders outside the install directory. Boot autostart is **not supported** on Windows.

### Desktop window (optional)

From a venv install, after `pip install -e '.[desktop]' -c constraints.txt` (needs WebKitGTK on Linux):

```bash
python -m paperless_agent.desktop
```

The desktop wrapper respects `PAPERLESS_HOST`, `PAPERLESS_PORT`, and `PAPERLESS_LOG_LEVEL`.

### Manual setup

Use this if you cloned with `git` and skipped the installer (Unix shell shown; on Windows use `py -3 -m venv .venv` and `.\.venv\Scripts\Activate.ps1`):

```bash
git clone https://github.com/dpastoetter/paperlessagent.git
cd paperlessagent
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e . -c constraints.txt
cp .env.example .env   # skip if .env already exists
```

(`requirements.txt` is a thin wrapper around the same constrained install for the OS installers.)

If you already have a clone but no `.venv`, run the `venv` / `pip install` steps above (or re-run the OS installer) before starting the server.

## AI providers

Cloud providers are locked until you approve the **cloud processing disclaimer** in **Settings → AI provider** (document page images and text leave this machine). Local Ollama does not require that acknowledgement.

### OpenAI / ChatGPT

In the web UI, open **Settings → AI provider → Cloud (ChatGPT)** and choose one:

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

ChatGPT OAuth only supports Codex models (e.g. `gpt-5.6-luna` / `terra` / `sol`). Platform IDs like `gpt-4.1` are rejected — the app auto-falls back to `gpt-5.6-luna` in that case. ChatGPT OAuth cannot call Platform embedding APIs, so RAG uses a **local ONNX sentence embedding model** (`all-MiniLM-L6-v2` via Chroma). For OpenAI-hosted embeddings, save an API key. Fully local installs should use the Ollama provider with `nomic-embed-text`.

**Find details (metadata extract)** uses the Codex Responses streaming API. The pipeline asks for JSON only and parses fenced or partially wrapped replies; empty model output is reported distinctly from invalid JSON. Platform API-key mode can additionally request JSON object mode on chat completions.

### Gemini (alternative)

```bash
PAPERLESS_LLM_PROVIDER=gemini
GOOGLE_API_KEY=...
PAPERLESS_MODEL=gemini-flash-latest
PAPERLESS_EMBEDDING_MODEL=text-embedding-004
```

### Ollama (fully local)

Run everything on your own hardware — no cloud account, no API key. The agent, AI vision OCR, and RAG embeddings all go through your local Ollama server.

1. Install [Ollama](https://ollama.com/download) and make sure it is running (`ollama serve`), or use **Start Ollama** in Settings when the CLI is installed but the daemon is down.
2. Open the app → **Settings → AI provider → Local Ollama**.
3. Click **Pull required models** (defaults: multimodal `gemma3` + `nomic-embed-text`).

That writes the provider into `.env` and switches the running app — no manual restart for the provider change. You can still configure it by hand:

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

**User-local install (no sudo):** on Linux you can install the Ollama binary under `~/.local/bin` and libraries under `~/.local/lib/ollama`. If you use a user-local build, ensure `LD_LIBRARY_PATH` includes `~/.local/lib/ollama` when starting `ollama serve` (PaperlessAgent’s systemd autostart unit sets this automatically when that directory exists).

While Ollama is the active provider, PaperlessAgent checks that the daemon is reachable and listening (and that required models are present) before chat, OCR, or embedding work. Health reports `degraded` when the local instance is offline or models are missing. Settings shows:

- **CPU / GPU / CPU + GPU** — inferred from Ollama’s `/api/ps` (`size_vram` on loaded models)
- **Start Ollama** — launch the daemon (systemd unit when available, otherwise `ollama serve`)
- **Unload model** — free RAM/VRAM after a long OCR run
- **Restart Ollama** — recover from a stuck daemon (blocked while a file is processing unless you cancel first)

If `PAPERLESS_LLM_PROVIDER` is unset, the app auto-selects OpenAI when a key/Codex login is available, else Gemini when `GOOGLE_API_KEY` is set, else a **running local Ollama** when one responds on `OLLAMA_BASE_URL`. Set `PAPERLESS_SKIP_OLLAMA_PROBE=1` to skip the localhost probe during auto-detect (useful in tests or air-gapped installs).

## Run the web app

Always activate the project venv first so you use its `uvicorn` and packages, not the system ones:

```bash
cd ~/paperlessagent   # or your install directory
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

| Symptom | Fix |
| --- | --- |
| `.venv/bin/activate: No such file or directory` | Re-run the installer. On Debian/Ubuntu also install `python3-venv`, then remove a broken leftover with `rm -rf ~/paperlessagent/.venv` and install again |
| `ModuleNotFoundError: No module named 'fastapi'` | Same — dependencies were never installed into `.venv`, or the venv was not activated |
| Port already in use | Stop the other instance: `pkill -f "uvicorn app.main:app"`, or pick another port with `--port 8081` and `PAPERLESS_PORT=8081` |
| Ollama OCR times out on CPU | Raise `PAPERLESS_OLLAMA_OCR_PAGE_TIMEOUT` (default 900s) or lower `PAPERLESS_OLLAMA_OCR_MAX_IMAGE_PX` (default 1024); see [OCR tuning](#ocr-and-long-documents) |
| Find details fails with “invalid JSON” | Usually empty/malformed model output — retry the file; with ChatGPT OAuth confirm a Codex model is selected and you are signed in |

Uvicorn should bind to `127.0.0.1` (the default). The API has no user login; mutating routes also require a custom header the UI sends (CSRF hardening). **Non-loopback binds** (`0.0.0.0`, a LAN IP, etc.) are **refused at startup** unless you set `PAPERLESS_API_TOKEN` in `.env`. With a token configured, every `/api/*` route except `/api/health` requires `Authorization: Bearer …` or the `pa_session` cookie (the UI bootstraps this on loopback, or via `http://host:8080/?token=…` once). Host headers are allowlisted (`PAPERLESS_ALLOWED_HOSTS`, defaulting to localhost / loopback) to harden against DNS rebinding.

```bash
python -c "from paperless_agent.local_security import generate_api_token; print(generate_api_token())"
# → put the value in .env as PAPERLESS_API_TOKEN=…
```

Open [http://localhost:8080](http://localhost:8080).

### Autostart at boot (Linux + systemd)

**Settings → Autostart → Start PaperlessAgent when the system boots** installs a user systemd unit at `~/.config/systemd/user/paperlessagent.service`, runs `loginctl enable-linger` so the service can start without an interactive login, and starts the unit if port 8080 is free.

The unit reads your install’s `.env`, sets `DATA_DIR`, and uses the venv `uvicorn` from the project root. Customize bind address/port with `PAPERLESS_HOST` and `PAPERLESS_PORT` in `.env` before enabling autostart.

Manual control:

```bash
systemctl --user status paperlessagent.service
systemctl --user restart paperlessagent.service
journalctl --user -u paperlessagent.service -f
```

Autostart requires Linux with `systemctl --user`. It is hidden on other platforms.

## Typical flow

1. Upload a scan in **Inbox** (or copy files into your source folder)
2. Click **Process inbox** — or let the poller pick it up automatically
3. Watch the live workflow strip; use **Cancel** on the active file if needed, then **Retry** after it stops
4. Check **Review**: correct the proposal if needed, then **Approve & file** (duplicates are flagged with a match score)
5. Find the filed document in **Archive**, or ask questions in **Ask**

Filing rules (source folder, category → folder mapping, poll interval, review requirement) live in **Settings → Filing & scanning** and are stored in `data/settings.json`.

### Processing controls

| Action | Where | Behavior |
| --- | --- | --- |
| **Cancel** | Inbox queue (active file) | Cooperative cancel — in-flight LLM/Ollama HTTP calls are aborted; button shows “Cancelling…” until the job ends |
| **Retry** | Inbox queue (error/cancelled) | Re-queues one file; if that file is still active, cancel is requested first |
| **Unload model** | Settings → Local Ollama | Drops loaded Ollama weights to free memory |
| **Restart Ollama** | Settings → Local Ollama | Restarts the daemon; blocked while processing unless the active file is cancelled |

Progress updates stream over `GET /api/process/events` (SSE). The workflow UI mounts once and patches in place to avoid flicker during rapid updates. Hover a pipeline step for a short description of what that stage does (and live detail while it is running).

### Metadata & review fields

During **Extract**, the LLM returns JSON with:

| Field | Description |
| --- | --- |
| `doc_type` | One of your configured categories (built-in types include invoice, receipt, bank, tax, utility, insurance, letter, contract, certificate, employment, medical, id, education, travel, other). Defaults to `other` when omitted; aliases like `document_type` / `category` are normalized |
| `doc_date` | Document date when present |
| `subject` | Short topic or title |
| `parties` | Sender, recipient, issuer, etc. |
| `reference_ids` | Policy numbers, invoice numbers, case refs (list) |
| `amount` / `currency` | Only kept for financial categories |
| `summary` | Short plain-language summary |

The review form shows amount/currency only when the selected category is financial. Reference IDs are editable as a comma-separated list.

## OCR and long documents

Text recovery is **adaptive**. For each PDF page the agent assesses the embedded text layer first:

- **Fast** — use embedded text when anything usable is present; vision only if the page is nearly empty
- **Balanced** (default) — use embedded text when quality heuristics pass; vision for weak/garbled/scanned pages
- **Maximum** — always run AI vision OCR on every page (closest to the old always-vision behavior)

Image files always use vision. Change the mode under **Settings → Filing & scanning → OCR accuracy**, or set `PAPERLESS_OCR_MODE`.

By default, **all PDF pages** are considered (up to 128). Cloud providers run needed vision pages with **bounded concurrency** (`PAPERLESS_OCR_CONCURRENCY`, default 4). Local Ollama stays serial by default (`PAPERLESS_OCR_CONCURRENCY_OLLAMA=1`). Page images are downscaled before vision OCR.

Tune via `.env` (see `.env.example` for the full list):

| Variable | Default | Purpose |
| --- | --- | --- |
| `PAPERLESS_OCR_MODE` | `balanced` | `fast` \| `balanced` \| `maximum` (overrides Settings) |
| `PAPERLESS_OCR_CONCURRENCY` | `4` | Parallel vision pages for cloud providers |
| `PAPERLESS_OCR_CONCURRENCY_OLLAMA` | `1` | Parallel vision pages for local Ollama |
| `PAPERLESS_OCR_MAX_PAGES` | `0` (all) | Cap pages per document; `0` = all up to safety max |
| `PAPERLESS_OCR_SAFETY_MAX_PAGES` | `128` | Hard ceiling on page count |
| `PAPERLESS_OCR_DPI` | `200` | PDF render resolution |
| `PAPERLESS_OCR_PAGE_TIMEOUT` | `180` | Seconds per page (cloud providers) |
| `PAPERLESS_OLLAMA_OCR_PAGE_TIMEOUT` | `900` | Seconds per page (local Ollama — CPU can be slow) |
| `PAPERLESS_OCR_MAX_IMAGE_PX` | `1536` | Max width/height before vision OCR (cloud) |
| `PAPERLESS_OLLAMA_OCR_MAX_IMAGE_PX` | `1024` | Max width/height for local Ollama OCR |
| `PAPERLESS_OLLAMA_OCR_NUM_CTX` | `4096` | Ollama context for OCR calls |
| `PAPERLESS_OLLAMA_OCR_NUM_PREDICT` | `4096` | Max tokens per OCR page (Ollama) |
| `PAPERLESS_OLLAMA_VISION_NUM_CTX` | `8192` | Ollama context for other vision calls |
| `PAPERLESS_OLLAMA_VISION_NUM_PREDICT` | `8192` | Max tokens for other vision calls |
| `PAPERLESS_EXTRACT_MAX_CHARS` | `48000` | OCR text sampled for metadata extraction (head + tail when longer) |

LLM timeouts (chat, vision, Ollama) and cancel join behavior:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PAPERLESS_LLM_TIMEOUT` | `120` | Text completion timeout (seconds) |
| `PAPERLESS_LLM_VISION_TIMEOUT` | `300` | Cloud vision timeout |
| `PAPERLESS_OLLAMA_TIMEOUT` | `300` | Ollama chat/vision timeout |
| `PAPERLESS_CANCEL_JOIN_TIMEOUT` | `3` | Max wait when joining a cancelled async LLM call |

If you switch embedding providers or models (Gemini / OpenAI / Ollama / local ONNX), the app **detects the mismatch automatically** (stored `embedding_provider`, `model`, `dimension`, and index schema version in `data/chroma/index_meta.json`) and rebuilds the vector index on the next index or Ask request. You do not need to remember to re-index manually.

**Ask grounding:** answers use only confident semantic hits (cosine distance ≤ `PAPERLESS_ASK_MAX_CHUNK_DISTANCE`, default `0.55`) plus SQLite FTS5 / metadata matches. If nothing relevant is retrieved, Ask says there isn’t enough evidence — it does **not** fall back to recent unrelated documents.

## Updates

**Settings → Software update** checks the GitHub releases of this repository and can download and install the latest version in place. Your documents, database, settings, and credentials (`data/`, `.env`) are never touched by an update.

Installs are **fail-closed on integrity checks**: the updater only applies a release that includes a `.tar.gz` asset with a SHA-256 digest (GitHub asset `digest` and/or a `SHA256SUMS` file). Tag-only or checksum-less releases are shown but refused at install time. Update checks retry on transient network failures.

Override the release source with `PAPERLESS_UPDATE_REPO=owner/repo` if you fork the project.

Pushing a version tag (`v*`) runs GitHub Actions, which builds the tarball and `SHA256SUMS`, then publishes/updates the GitHub Release. The packager archives the **exact tagged commit** (not a dirty working tree), verifies the file list against `git ls-tree`, and embeds `.release-commit` plus `.release-files` so installs/updates can confirm the SHA and prune stale paths.

**Release checklist:** land every change on `main` first, bump `version` in `pyproject.toml` (the single source for package metadata, OpenAPI/`FastAPI.version`, and the in-app updater), regenerate pins if dependencies changed (`./scripts/lock-deps.sh`), commit, then create the matching tag on that commit (`git tag v0.2.0 && git push origin v0.2.0`). Tagging an older commit is how earlier releases missed later work.

Local dry-run:

```bash
git checkout v0.2.0
./scripts/make-release-assets.sh v0.2.0
# or before the tag exists:
./scripts/make-release-assets.sh v0.2.0 HEAD
# dist/ contains paperlessagent-0.2.0.tar.gz, install.sh, install.ps1, SHA256SUMS
```

## Mockup mode

**Settings → Look & feel → Mockup mode** fills every view with demo data — useful for screenshots and demos without exposing personal documents. It is client-side only: while enabled, no real data is read or written, and all mutating actions are blocked. Also available ad hoc via URL parameters: `http://localhost:8080/?mock=1&theme=slate#/archive`.

## Development

### Tests & CI quality gate

```bash
pip install -e ".[dev]" -c constraints.txt   # pytest, pytest-cov, ruff, mypy, pip-tools
./scripts/ci.sh           # same gate as GitHub Actions (format, lint, pip check, mypy, JS, Vitest, pytest+coverage)
./scripts/precommit.sh    # secret guard + CI gate (also usable as a git hook)
```

Coverage is measured with `pytest-cov` (`paperless_agent`, `app`, `query_agent`; branch coverage). CI fails if total coverage drops below the floor in `pyproject.toml` (`--cov-fail-under`). HTML report: `htmlcov/` (gitignored). Desktop GUI (`desktop.py`) and live network/Ollama/systemd are mocked or omitted — unit-test their helpers instead.

Pure frontend helpers (`app/static/api.js`, `router.js`, `state.js`) have a small Vitest harness (`npm test`). Install Node deps once with `npm install`; CI runs the same suite after the JS syntax check.

Install the pre-commit hook once per clone so the gate runs on every commit:

```bash
ln -sf ../../scripts/precommit.sh .git/hooks/pre-commit
```

The pre-commit gate refuses commits containing `.env` files, databases, `data/` content, or anything that looks like an API key.

### Dependencies

`pyproject.toml` is the source of truth for direct dependencies. Exact transitive versions are pinned in `constraints.txt` (regenerate with `./scripts/lock-deps.sh` after changing deps).

| Install | Command |
| --- | --- |
| Runtime | `pip install -e . -c constraints.txt` |
| Desktop shell | `pip install -e '.[desktop]' -c constraints.txt` |
| Dev tools | `pip install -e '.[dev]' -c constraints.txt` |

Installers still call `pip install -r requirements.txt` / `requirements-desktop.txt`, which are thin wrappers around those constrained editable installs (not a second dependency list).

Pull requests and pushes to `main` run [`.github/workflows/ci.yml`](.github/workflows/ci.yml). Tag releases run the same quality gate on the exact tagged commit before publishing assets ([`.github/workflows/release.yml`](.github/workflows/release.yml)).

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
  ocr.py               #   adaptive text-layer / vision OCR, PDF render, image prep
  llm.py               #   OpenAI / Codex OAuth / Gemini / Ollama backends + cancel
  providers/           #   LlmProvider interface (text, vision, embeddings, health, usage)
  progress.py          #   SSE progress + pipeline step labels/descriptions
  job_control.py       #   per-file cancel events for ingest
  review.py            #   human-in-the-loop queue (approve / reject)
  dedup.py             #   checksum + content-hash + similarity duplicate detection
  ollama_setup.py      #   Ollama probe, model pull, CPU/GPU summary, provider switch
  system_service.py    #   systemd user unit for boot autostart
  updater.py           #   self-update from GitHub releases
query_agent/           # RAG Q&A agent
app/                   # FastAPI app (main.py + routers/) and ES-module UI (static/)
  routers/             #   documents, reviews, settings, processing, auth, updates
  schemas.py           #   request/response models
  static/              #   api.js, inbox.js, review.js, settings.js, events.js, …
scripts/               # install.sh, install.ps1, make-release-assets.sh, ci.sh, precommit.sh, watch_inbox.py
tests/                 # pytest (Python) + tests/frontend (Vitest)
docs/screenshots/      # README screenshots (generated with mockup mode)
docs/deck/             # product slide deck (open index.html in a browser)
package.json           # Vitest for pure ES-module UI helpers
data/                  # created at runtime (gitignored)
```

Regenerate README screenshots (server must be running on port 8080):

```bash
# uvicorn app.main:app --port 8080
npx playwright install chromium   # once
node scripts/capture-screenshots.mjs
```

## Configuration reference

All optional variables are documented in `.env.example`. Common entries:

```bash
DATA_DIR=./data
PAPERLESS_HOST=127.0.0.1
PAPERLESS_PORT=8080
PAPERLESS_LLM_PROVIDER=ollama   # gemini | openai | ollama
PAPERLESS_MODEL=gemma3
PAPERLESS_EMBEDDING_MODEL=nomic-embed-text
OLLAMA_BASE_URL=http://localhost:11434
```

Runtime settings (inbox path, categories, poll interval, review requirement) are edited in the web UI and stored in `data/settings.json`, not in `.env`.

## License

[Apache License 2.0](LICENSE) — Copyright 2026 dpastoetter
