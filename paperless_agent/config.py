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

# Local Ollama server (used when LLM_PROVIDER == "ollama").
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def _ollama_reachable_quick(url: str) -> bool:
    """Short localhost probe used only during provider auto-detect."""
    try:
        import httpx

        with httpx.Client(timeout=0.6) as client:
            return client.get(f"{url.rstrip('/')}/api/tags").is_success
    except Exception:  # noqa: BLE001 — auto-detect must never break startup
        return False


def _resolve_provider() -> str:
    """Pick gemini | openai | ollama from env, credentials, or a live local Ollama."""
    raw = os.getenv("PAPERLESS_LLM_PROVIDER", "").strip().lower()
    if raw in {"gemini", "google"}:
        return "gemini"
    if raw in {"openai", "codex"}:
        return "openai"
    if raw in {"ollama", "local"}:
        return "ollama"

    from paperless_agent.auth import resolve_openai_api_key

    if resolve_openai_api_key():
        return "openai"
    if os.getenv("GOOGLE_API_KEY", "").strip():
        return "gemini"

    # No cloud credentials — prefer a running local Ollama when present.
    skip_probe = os.getenv("PAPERLESS_SKIP_OLLAMA_PROBE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if not skip_probe and _ollama_reachable_quick(OLLAMA_BASE_URL):
        return "ollama"

    return "gemini"

# gemini | openai | ollama  (openai can use OPENAI_API_KEY or Codex API-key login)
LLM_PROVIDER = _resolve_provider()

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
    "bank",
    "tax",
    "utility",
    "insurance",
    "letter",
    "contract",
    "certificate",
    "employment",
    "medical",
    "id",
    "education",
    "travel",
    "other",
)

# Categories where amount/currency metadata is meaningful.
FINANCIAL_DOC_TYPES = frozenset(
    {"invoice", "receipt", "bank", "tax", "utility", "insurance"}
)


def is_financial_doc_type(doc_type: str | None) -> bool:
    """True when monetary fields are relevant for this document category."""
    return (doc_type or "other").strip().lower() in FINANCIAL_DOC_TYPES


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return int(raw)


# OCR / vision — 0 means all pages (clamped to OCR_SAFETY_MAX_PAGES).
OCR_MAX_PAGES = _env_int("PAPERLESS_OCR_MAX_PAGES", 0)
OCR_SAFETY_MAX_PAGES = _env_int("PAPERLESS_OCR_SAFETY_MAX_PAGES", 128)
OCR_DPI = _env_int("PAPERLESS_OCR_DPI", 200)
OCR_PAGE_TIMEOUT = float(os.getenv("PAPERLESS_OCR_PAGE_TIMEOUT", "180"))
# Local Ollama on CPU can exceed 180s/page; use a separate budget for vision OCR.
OLLAMA_OCR_PAGE_TIMEOUT = float(os.getenv("PAPERLESS_OLLAMA_OCR_PAGE_TIMEOUT", "600"))
OCR_MAX_IMAGE_PX = _env_int("PAPERLESS_OCR_MAX_IMAGE_PX", 1536)
OLLAMA_VISION_NUM_CTX = _env_int("PAPERLESS_OLLAMA_VISION_NUM_CTX", 8192)
OLLAMA_VISION_NUM_PREDICT = _env_int("PAPERLESS_OLLAMA_VISION_NUM_PREDICT", 8192)
OLLAMA_OCR_NUM_CTX = _env_int("PAPERLESS_OLLAMA_OCR_NUM_CTX", 4096)
OLLAMA_OCR_NUM_PREDICT = _env_int("PAPERLESS_OLLAMA_OCR_NUM_PREDICT", 2048)
EXTRACT_MAX_CHARS = _env_int("PAPERLESS_EXTRACT_MAX_CHARS", 48000)


def ensure_data_dirs() -> None:
    """Create local storage directories if missing."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
