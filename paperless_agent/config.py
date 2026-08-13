"""Runtime configuration for PaperlessAgent."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from paperless_agent.ollama_url import (
    allow_remote_ollama_enabled,
    is_loopback_ollama_url,
    require_ollama_base_url,
)

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

        # Auto-detect must never SSRF: only probe loopback unless remote is opted in.
        if not is_loopback_ollama_url(url) and not allow_remote_ollama_enabled():
            return False
        safe = require_ollama_base_url(url)
        with httpx.Client(timeout=0.6, follow_redirects=False) as client:
            return client.get(f"{safe}/api/tags").is_success
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
# Cosine distance ceiling for Ask grounding (Chroma cosine space: 0 = identical).
# Chunks farther than this are treated as irrelevant and dropped.
ASK_MAX_CHUNK_DISTANCE = float(os.getenv("PAPERLESS_ASK_MAX_CHUNK_DISTANCE", "0.55"))

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
FINANCIAL_DOC_TYPES = frozenset({"invoice", "receipt", "bank", "tax", "utility", "insurance"})


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
OLLAMA_OCR_PAGE_TIMEOUT = float(os.getenv("PAPERLESS_OLLAMA_OCR_PAGE_TIMEOUT", "900"))
OCR_MAX_IMAGE_PX = _env_int("PAPERLESS_OCR_MAX_IMAGE_PX", 1536)
# Ollama on CPU is much slower on large page images; cap separately unless overridden.
OLLAMA_OCR_MAX_IMAGE_PX = _env_int("PAPERLESS_OLLAMA_OCR_MAX_IMAGE_PX", 1024)
OLLAMA_VISION_NUM_CTX = _env_int("PAPERLESS_OLLAMA_VISION_NUM_CTX", 8192)
OLLAMA_VISION_NUM_PREDICT = _env_int("PAPERLESS_OLLAMA_VISION_NUM_PREDICT", 8192)
OLLAMA_OCR_NUM_CTX = _env_int("PAPERLESS_OLLAMA_OCR_NUM_CTX", 4096)
OLLAMA_OCR_NUM_PREDICT = _env_int("PAPERLESS_OLLAMA_OCR_NUM_PREDICT", 4096)
# Adaptive OCR: fast | balanced | maximum (settings.json can override).
OCR_MODE = os.getenv("PAPERLESS_OCR_MODE", "balanced").strip().lower() or "balanced"
# Parallel vision pages for cloud providers; Ollama stays serial by default.
OCR_CONCURRENCY = _env_int("PAPERLESS_OCR_CONCURRENCY", 4)
OCR_CONCURRENCY_OLLAMA = _env_int("PAPERLESS_OCR_CONCURRENCY_OLLAMA", 1)
EXTRACT_MAX_CHARS = _env_int("PAPERLESS_EXTRACT_MAX_CHARS", 48000)

# Untrusted media hardening (upload + OCR).
MEDIA_MAX_IMAGE_PIXELS = _env_int("PAPERLESS_MEDIA_MAX_IMAGE_PIXELS", 40_000_000)
MEDIA_MAX_PDF_PAGES = _env_int("PAPERLESS_MEDIA_MAX_PDF_PAGES", OCR_SAFETY_MAX_PAGES)
# ~200 inches at 72 pt/inch — rejects absurd MediaBox decompression bombs.
MEDIA_MAX_PDF_PAGE_POINTS = float(os.getenv("PAPERLESS_MEDIA_MAX_PDF_PAGE_POINTS", "14400"))
MEDIA_WORKER_TIMEOUT_S = float(os.getenv("PAPERLESS_MEDIA_WORKER_TIMEOUT_S", "90"))
MEDIA_WORKER_MEMORY_MB = _env_int("PAPERLESS_MEDIA_WORKER_MEMORY_MB", 1024)
MEDIA_WORKER_CPU_S = _env_int("PAPERLESS_MEDIA_WORKER_CPU_S", 60)


def ensure_data_dirs() -> None:
    """Create local storage directories if missing."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
