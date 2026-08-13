"""Filesystem helpers: list/clear inbox and reveal_in_explorer (mocked OS)."""

from __future__ import annotations

from paperless_agent.settings import get_source_dir
from paperless_agent.tools import filesystem


def test_list_and_clear_inbox(isolated_data):
    inbox = get_source_dir()
    pdf = inbox / "a.pdf"
    png = inbox / "b.png"
    junk = inbox / "notes.txt"
    pdf.write_bytes(b"%PDF")
    png.write_bytes(b"\x89PNG")
    junk.write_text("ignore")

    listed = filesystem.list_inbox()
    assert listed["count"] == 2
    names = {f["name"] for f in listed["files"]}
    assert names == {"a.pdf", "b.png"}

    cleared = filesystem.clear_inbox()
    assert cleared["status"] == "success"
    assert cleared["removed_count"] == 2
    assert set(cleared["removed"]) == {"a.pdf", "b.png"}
    assert filesystem.list_inbox()["count"] == 0
    assert junk.exists()


def test_reveal_in_explorer_missing(isolated_data):
    result = filesystem.reveal_in_explorer(str(isolated_data / "missing.pdf"))
    assert result["status"] == "error"
    assert "not found" in result["error"].lower()


def test_reveal_in_explorer_linux(isolated_data, monkeypatch):
    target = isolated_data / "show.pdf"
    target.write_bytes(b"%PDF")
    launched: list[list[str]] = []

    monkeypatch.setattr(filesystem.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        filesystem.shutil,
        "which",
        lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None,
    )

    def fake_popen(cmd, **_kw):
        launched.append(list(cmd))
        return None

    monkeypatch.setattr(filesystem.subprocess, "Popen", fake_popen)
    result = filesystem.reveal_in_explorer(str(target))
    assert result["status"] == "success"
    assert launched
    assert launched[0][0] == "/usr/bin/xdg-open"


def test_reveal_in_explorer_darwin(isolated_data, monkeypatch):
    target = isolated_data / "mac.pdf"
    target.write_bytes(b"%PDF")
    launched: list[list[str]] = []

    monkeypatch.setattr(filesystem.platform, "system", lambda: "Darwin")

    def fake_popen(cmd, **_kw):
        launched.append(list(cmd))
        return None

    monkeypatch.setattr(filesystem.subprocess, "Popen", fake_popen)
    result = filesystem.reveal_in_explorer(str(target))
    assert result["status"] == "success"
    assert launched[0][:2] == ["open", "-R"]


def test_read_document_image_notes_vision(isolated_data):
    img = isolated_data / "scan.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    result = filesystem.read_document(str(img))
    assert result["status"] == "success"
    assert result.get("suffix") == ".png" or "image" in str(result).lower()
