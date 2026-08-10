"""Filesystem helpers and ADK tools for reading/filing documents."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from paperless_agent.config import ensure_data_dirs
from paperless_agent.settings import get_folder_for_category, get_source_dir

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
PDF_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | PDF_SUFFIXES


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", value.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned[:120] or "document"


def list_inbox() -> dict[str, Any]:
    """List supported scan files waiting in the configured source folder."""
    ensure_data_dirs()
    inbox = get_source_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    files = sorted(
        [
            {
                "name": p.name,
                "path": str(p.resolve()),
                "suffix": p.suffix.lower(),
                "size_bytes": p.stat().st_size,
            }
            for p in inbox.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
        ],
        key=lambda item: item["name"],
    )
    return {
        "status": "success",
        "count": len(files),
        "files": files,
        "source_dir": str(inbox),
    }


def clear_inbox() -> dict[str, Any]:
    """Delete all supported scan files from the configured source folder."""
    ensure_data_dirs()
    inbox = get_source_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    removed: list[str] = []
    for path in sorted(inbox.iterdir()):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            path.unlink()
            removed.append(path.name)
    return {
        "status": "success",
        "removed_count": len(removed),
        "removed": removed,
        "source_dir": str(inbox),
    }


def reveal_in_explorer(path: str) -> dict[str, Any]:
    """
    Reveal a local file in the system file manager (or open its folder).

    On Linux this prefers selecting the file in Nautilus/Dolphin when available,
    otherwise opens the parent folder with xdg-open.
    """
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        return {"status": "error", "error": f"file not found: {path}"}

    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(  # noqa: S603
                ["open", "-R", str(file_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        elif system == "Windows":
            subprocess.Popen(  # noqa: S603
                ["explorer", "/select,", str(file_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        else:
            # Linux / FreeDesktop
            parent = str(file_path.parent)
            launched = False
            for cmd in (
                ["nautilus", "--select", str(file_path)],
                ["dolphin", "--select", str(file_path)],
                ["nemo", str(file_path)],
                ["xdg-open", parent],
            ):
                exe = shutil.which(cmd[0])
                if not exe:
                    continue
                subprocess.Popen(  # noqa: S603
                    [exe, *cmd[1:]],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    env=os.environ.copy(),
                )
                launched = True
                break
            if not launched:
                return {
                    "status": "error",
                    "error": "No file manager found (tried nautilus, dolphin, nemo, xdg-open)",
                }
    except OSError as exc:
        return {"status": "error", "error": str(exc)}

    return {
        "status": "success",
        "path": str(file_path),
        "opened": "explorer",
    }


def read_document(path: str) -> dict[str, Any]:
    """
    Read a local PDF or image for classification/extraction.

    For PDFs, returns extracted text when available. Scanned PDFs may have
    empty text; agents should still classify from filename/context and any
    available text. Images return metadata and note that vision OCR is needed.
    """
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists() or not file_path.is_file():
        return {"status": "error", "error": f"file not found: {path}"}

    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return {
            "status": "error",
            "error": f"unsupported file type: {suffix}",
            "supported": sorted(SUPPORTED_SUFFIXES),
        }

    result: dict[str, Any] = {
        "status": "success",
        "path": str(file_path),
        "filename": file_path.name,
        "suffix": suffix,
        "size_bytes": file_path.stat().st_size,
        "text": "",
        "page_count": None,
        "is_image": suffix in IMAGE_SUFFIXES,
        "is_pdf": suffix in PDF_SUFFIXES,
    }

    if suffix in PDF_SUFFIXES:
        try:
            reader = PdfReader(str(file_path))
            pages = []
            for i, page in enumerate(reader.pages):
                page_text = page.extract_text() or ""
                pages.append({"page": i + 1, "text": page_text})
            result["page_count"] = len(pages)
            result["pages"] = pages
            result["text"] = "\n\n".join(
                f"[Page {p['page']}]\n{p['text']}".strip() for p in pages
            ).strip()
            if not result["text"]:
                result["note"] = (
                    "No extractable text layer; treat as scanned PDF and infer "
                    "from available cues / multimodal analysis."
                )
        except Exception as exc:  # noqa: BLE001 - surface to agent
            return {"status": "error", "error": f"failed to read PDF: {exc}"}
    else:
        result["note"] = (
            "Image scan; no text layer. Use multimodal reasoning on the file "
            "path/name and any known context."
        )

    return result


def propose_filename(
    doc_type: str,
    doc_date: str | None = None,
    counterparty: str | None = None,
    amount: float | None = None,
    currency: str | None = None,
    original_path: str | None = None,
    extension: str | None = None,
) -> dict[str, Any]:
    """
    Build a collision-safe meaningful filename.

    Example: 2024-03-15_Invoice_Acme_EUR120.pdf
    """
    date_part = (doc_date or "undated").replace("/", "-")
    type_part = _safe_stem((doc_type or "other").title())
    party_part = _safe_stem(counterparty) if counterparty else "Unknown"
    amount_part = ""
    if amount is not None:
        cur = (currency or "EUR").upper()
        # Avoid decimals in filenames when whole euros/cents not critical
        if float(amount).is_integer():
            amount_part = f"_{cur}{int(amount)}"
        else:
            amount_part = f"_{cur}{amount:.2f}".replace(".", "p")

    if extension:
        ext = extension if extension.startswith(".") else f".{extension}"
    elif original_path:
        ext = Path(original_path).suffix.lower() or ".pdf"
    else:
        ext = ".pdf"

    base = f"{date_part}_{type_part}_{party_part}{amount_part}"
    filename = f"{_safe_stem(base)}{ext}"
    return {"status": "success", "filename": filename}


def move_to_archive(
    source_path: str,
    filename: str,
    doc_type: str = "other",
    year: str | None = None,
) -> dict[str, Any]:
    """
    Move a file into {category_folder}/{yyyy}/ with the given filename.

    Category folders come from Setup settings; unknown types fall back to 'other'.
    Uses a numeric suffix if the destination already exists.
    """
    ensure_data_dirs()
    src = Path(source_path).expanduser().resolve()
    if not src.exists() or not src.is_file():
        return {"status": "error", "error": f"source not found: {source_path}"}

    safe_type = _safe_stem(doc_type or "other").lower()
    year_part = year or "unknown"
    if year_part != "unknown" and not re.fullmatch(r"\d{4}", year_part):
        # Accept YYYY-MM-DD and extract year
        match = re.match(r"(\d{4})", year_part)
        year_part = match.group(1) if match else "unknown"

    category_root = get_folder_for_category(safe_type)
    dest_dir = category_root / year_part
    dest_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(filename).name
    dest = dest_dir / safe_name
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        n = 2
        while True:
            candidate = dest_dir / f"{stem}_{n}{suffix}"
            if not candidate.exists():
                dest = candidate
                break
            n += 1

    shutil.move(str(src), str(dest))
    return {
        "status": "success",
        "archive_path": str(dest.resolve()),
        "filename": dest.name,
        "doc_type": safe_type,
        "year": year_part,
        "category_folder": str(category_root),
    }


def save_upload_to_inbox(filename: str, content: bytes) -> dict[str, Any]:
    """Persist an uploaded file into the configured source folder."""
    ensure_data_dirs()
    inbox = get_source_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    dest = inbox / safe_name
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        n = 2
        while True:
            candidate = inbox / f"{stem}_{n}{suffix}"
            if not candidate.exists():
                dest = candidate
                break
            n += 1
    dest.write_bytes(content)
    return {
        "status": "success",
        "path": str(dest.resolve()),
        "filename": dest.name,
        "source_dir": str(inbox),
    }
