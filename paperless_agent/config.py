"""Runtime configuration for PaperlessAgent."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", PROJECT_ROOT / "data")).expanduser().resolve()
INBOX_DIR = DATA_DIR / "inbox"
ARCHIVE_DIR = DATA_DIR / "archive"
DB_PATH = DATA_DIR / "paperless.db"
CHROMA_DIR = DATA_DIR / "chroma"

# gemini | openai  (openai can use OPENAI_API_KEY or Codex API-key login)
_raw_provider = os.getenv("PAPERLESS_LLM_PROVIDER", "").strip().lower()
if _raw_provider in {"gemini", "google"}:
    LLM_PROVIDER = "gemini"
elif _raw_provider in {"openai", "codex"}:
    LLM_PROVIDER = "openai"
else:
    # Auto: prefer OpenAI/Codex when a key is already available
    from paperless_agent.auth import resolve_openai_api_key

    if resolve_openai_api_key():
        LLM_PROVIDER = "openai"
    elif os.getenv("GOOGLE_API_KEY", "").strip():
        LLM_PROVIDER = "gemini"
    else:
        LLM_PROVIDER = "gemini"

# Default to a Codex-compatible model so ChatGPT OAuth works out of the box.
# Override with PAPERLESS_MODEL (API) or PAPERLESS_CODEX_MODEL (ChatGPT OAuth).
_default_model = "gpt-5.6-luna" if LLM_PROVIDER == "openai" else "gemini-flash-latest"
_default_embedding = (
    "text-embedding-3-small"
    if LLM_PROVIDER == "openai"
    else "text-embedding-004"
)

MODEL_NAME = os.getenv("PAPERLESS_MODEL", _default_model)
EMBEDDING_MODEL = os.getenv("PAPERLESS_EMBEDDING_MODEL", _default_embedding)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

CHUNK_SIZE_CHARS = 3200  # ~800 tokens rough estimate
CHUNK_OVERLAP_CHARS = 400
RETRIEVE_TOP_K = 6

DOC_TYPES = (
    "invoice",
    "receipt",
    "contract",
    "letter",
    "tax",
    "medical",
    "id",
    "other",
)


def ensure_data_dirs() -> None:
    """Create local storage directories if missing."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
