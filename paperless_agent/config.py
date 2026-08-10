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

# gemini | openai | ollama  (openai can use OPENAI_API_KEY or Codex API-key login)
_raw_provider = os.getenv("PAPERLESS_LLM_PROVIDER", "").strip().lower()
if _raw_provider in {"gemini", "google"}:
    LLM_PROVIDER = "gemini"
elif _raw_provider in {"openai", "codex"}:
    LLM_PROVIDER = "openai"
elif _raw_provider in {"ollama", "local"}:
    LLM_PROVIDER = "ollama"
else:
    # Auto: prefer OpenAI/Codex when a key is already available
    from paperless_agent.auth import resolve_openai_api_key

    if resolve_openai_api_key():
        LLM_PROVIDER = "openai"
    elif os.getenv("GOOGLE_API_KEY", "").strip():
        LLM_PROVIDER = "gemini"
    else:
        LLM_PROVIDER = "gemini"

# Local Ollama server (only used when LLM_PROVIDER == "ollama").
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")

# Default to a Codex-compatible model so ChatGPT OAuth works out of the box.
# Override with PAPERLESS_MODEL (API) or PAPERLESS_CODEX_MODEL (ChatGPT OAuth).
if LLM_PROVIDER == "openai":
    _default_model = "gpt-5.6-luna"
    _default_embedding = "text-embedding-3-small"
elif LLM_PROVIDER == "ollama":
    # Needs a multimodal model: AI vision OCR sends page images to the same model.
    _default_model = "gemma3"
    _default_embedding = "nomic-embed-text"
else:
    _default_model = "gemini-flash-latest"
    _default_embedding = "text-embedding-004"

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
