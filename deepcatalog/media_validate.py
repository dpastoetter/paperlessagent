"""Validate uploaded PDFs/images by content, not just filename suffix."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from deepcatalog import config

# Keep sniffing cheap — enough for common scan formats.
_SNIFF_BYTES = 32

_KIND_BY_SUFFIX: dict[str, frozenset[str]] = {
    ".pdf": frozenset({"pdf"}),
    ".png": frozenset({"png"}),
    ".jpg": frozenset({"jpeg"}),
    ".jpeg": frozenset({"jpeg"}),
    ".webp": frozenset({"webp"}),
    ".tif": frozenset({"tiff"}),
    ".tiff": frozenset({"tiff"}),
    ".bmp": frozenset({"bmp"}),
}


class MediaValidationError(ValueError):
    """Reject untrusted or malformed scan media."""

    def __init__(self, message: str, *, code: str = "invalid_media") -> None:
        super().__init__(message)
        self.code = code


def sniff_media_kind(header: bytes) -> str | None:
    """Return a media kind from magic bytes, or None when unrecognized."""
    if len(header) < 4:
        return None
    if header.startswith(b"%PDF"):
        return "pdf"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith(b"BM"):
        return "bmp"
    if header[:4] in {b"II*\x00", b"MM\x00*"}:
        return "tiff"
    if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "webp"
    return None


def expected_kinds_for_suffix(suffix: str) -> frozenset[str]:
    return _KIND_BY_SUFFIX.get(suffix.lower(), frozenset())


def _read_header(path: Path, n: int = _SNIFF_BYTES) -> bytes:
    with path.open("rb") as fh:
        return fh.read(n)


def _page_box_points(page: Any) -> tuple[float, float] | None:
    box = getattr(page, "mediabox", None) or getattr(page, "mediaBox", None)
    if box is None:
        return None
    try:
        width = float(box.width)
        height = float(box.height)
    except Exception:  # noqa: BLE001 — malformed box
        return None
    return width, height


def validate_pdf_structure(path: Path) -> dict[str, Any]:
    """Reject encrypted, unparseable, or absurd PDFs before OCR/parsing work."""
    try:
        reader = PdfReader(str(path), strict=False)
    except (PdfReadError, OSError, ValueError) as exc:
        raise MediaValidationError(
            f"unparseable PDF: {exc}",
            code="unparseable",
        ) from exc

    if getattr(reader, "is_encrypted", False):
        # Try empty password; still treat as encrypted if locked.
        unlocked = False
        try:
            unlocked = bool(reader.decrypt(""))
        except Exception:  # noqa: BLE001
            unlocked = False
        if not unlocked:
            raise MediaValidationError("encrypted PDFs are not supported", code="encrypted")

    try:
        page_count = len(reader.pages)
    except Exception as exc:  # noqa: BLE001
        raise MediaValidationError(f"unparseable PDF pages: {exc}", code="unparseable") from exc

    max_pages = max(1, int(config.MEDIA_MAX_PDF_PAGES))
    if page_count <= 0:
        raise MediaValidationError("PDF has no pages", code="empty")
    if page_count > max_pages:
        raise MediaValidationError(
            f"PDF has too many pages ({page_count}; max {max_pages})",
            code="too_many_pages",
        )

    max_pts = float(config.MEDIA_MAX_PDF_PAGE_POINTS)
    for index, page in enumerate(reader.pages):
        size = _page_box_points(page)
        if size is None:
            continue
        width, height = size
        if width <= 0 or height <= 0:
            raise MediaValidationError(
                f"PDF page {index + 1} has invalid MediaBox",
                code="bad_page_size",
            )
        if width > max_pts or height > max_pts:
            raise MediaValidationError(
                f"PDF page {index + 1} MediaBox is too large "
                f"({width:.0f}×{height:.0f} pts; max {max_pts:.0f})",
                code="bad_page_size",
            )

    return {"kind": "pdf", "page_count": page_count}


def validate_image_structure(path: Path, *, expected_kind: str | None = None) -> dict[str, Any]:
    """
    Inspect image headers/dimensions without trusting the filename.

    Uses Pillow's lazy header parse for size, then enforces a pixel ceiling before
    a full decode (``load``).
    """
    max_pixels = max(1, int(config.MEDIA_MAX_IMAGE_PIXELS))
    # Pillow decompression-bomb guard (also covers later full decodes).
    Image.MAX_IMAGE_PIXELS = max_pixels

    try:
        with Image.open(path) as img:
            width, height = img.size
            fmt = (img.format or "").lower() or None
            if expected_kind and fmt and fmt.lower() != expected_kind.lower():
                raise MediaValidationError(
                    f"image format {fmt!r} does not match content type {expected_kind!r}",
                    code="type_mismatch",
                )
            if width <= 0 or height <= 0:
                raise MediaValidationError("image has invalid dimensions", code="bad_image_size")
            pixels = width * height
            if pixels > max_pixels:
                raise MediaValidationError(
                    f"image too large ({width}×{height} = {pixels} pixels; max {max_pixels})",
                    code="too_many_pixels",
                )
            # Force a bounded decode so truncated/corrupt payloads fail at upload time.
            img.load()
            kind = (fmt or expected_kind or "image").lower()
            return {"kind": kind, "width": width, "height": height, "pixels": pixels}
    except MediaValidationError:
        raise
    except UnidentifiedImageError as exc:
        raise MediaValidationError("unrecognized or corrupt image", code="unparseable") from exc
    except Image.DecompressionBombError as exc:
        raise MediaValidationError(
            f"image exceeds pixel limit ({max_pixels})",
            code="too_many_pixels",
        ) from exc
    except OSError as exc:
        raise MediaValidationError(f"unreadable image: {exc}", code="unparseable") from exc


def validate_scan_file(path: Path | str, *, suffix: str | None = None) -> dict[str, Any]:
    """
    Validate a PDF/image after upload or before OCR.

    Checks magic bytes against the filename suffix, then structural limits.
    ``suffix`` overrides ``path.suffix`` so ``.part`` upload temps can be checked
    against the intended destination extension.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise MediaValidationError(f"file not found: {file_path}", code="missing")

    check_suffix = (suffix or file_path.suffix).lower()
    if not check_suffix.startswith("."):
        check_suffix = f".{check_suffix}"
    expected = expected_kinds_for_suffix(check_suffix)
    if not expected:
        raise MediaValidationError(
            f"unsupported file type: {check_suffix or 'none'}",
            code="unsupported",
        )

    header = _read_header(file_path)
    kind = sniff_media_kind(header)
    if kind is None:
        raise MediaValidationError(
            "file content does not match a supported PDF/image type",
            code="type_mismatch",
        )
    if kind not in expected:
        raise MediaValidationError(
            f"file content looks like {kind}, but filename suffix is {check_suffix}",
            code="type_mismatch",
        )

    if kind == "pdf":
        info = validate_pdf_structure(file_path)
    else:
        info = validate_image_structure(file_path, expected_kind=kind)

    info["path"] = str(file_path.resolve())
    info["suffix"] = check_suffix
    info["sniffed_kind"] = kind
    return info
