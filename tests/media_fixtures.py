"""Shared fixtures for media / upload tests."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image


def write_minimal_pdf(path: Path, line: str = "Hello DeepCatalog") -> Path:
    """Write a tiny valid one-page PDF (Helvetica text)."""
    content = f"BT /F1 12 Tf 100 700 Td ({line}) Tj ET"
    stream = content.encode("latin-1", errors="replace")
    objects = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
        (
            b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
        ),
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode() + stream + b"\nendstream\nendobj\n",
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.extend(f"{off:010d} 00000 n \n".encode())
    pdf.extend(
        f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    path.write_bytes(pdf)
    return path


def minimal_pdf_bytes(line: str = "Hello DeepCatalog") -> bytes:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = write_minimal_pdf(Path(tmp) / "doc.pdf", line=line)
        return path.read_bytes()


def write_minimal_png(path: Path, size: tuple[int, int] = (32, 24)) -> Path:
    image = Image.new("RGB", size, color=(240, 240, 240))
    image.save(path, format="PNG")
    return path


def minimal_png_bytes(size: tuple[int, int] = (32, 24)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=(240, 240, 240)).save(buf, format="PNG")
    return buf.getvalue()
