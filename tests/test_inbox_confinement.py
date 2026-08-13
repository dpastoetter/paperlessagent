"""Inbox path confinement for ADK filesystem tools."""

from __future__ import annotations

from pathlib import Path

from tests.media_fixtures import write_minimal_pdf, write_minimal_png

from paperless_agent.pipeline.agents import file_and_persist
from paperless_agent.settings import get_source_dir
from paperless_agent.tools import filesystem


def test_read_document_rejects_path_outside_inbox(isolated_data):
    outside = write_minimal_pdf(isolated_data.parent / "secret.pdf")
    result = filesystem.read_document(str(outside))
    assert result["status"] == "error"
    assert result.get("code") == "outside_inbox"
    assert "inbox" in result["error"].lower()
    assert outside.exists()


def test_move_to_archive_rejects_path_outside_inbox(isolated_data):
    outside = write_minimal_pdf(isolated_data.parent / "escape.pdf")
    result = filesystem.move_to_archive(
        source_path=str(outside),
        filename="2024-01-01_Other_Escape.pdf",
        doc_type="other",
        year="2024",
    )
    assert result["status"] == "error"
    assert result.get("code") == "outside_inbox"
    assert outside.exists()


def test_file_and_persist_rejects_path_outside_inbox(isolated_data, stub_rag_index):
    outside = write_minimal_pdf(isolated_data.parent / "home-doc.pdf")
    result = file_and_persist(
        source_path=str(outside),
        filename="2024-01-01_Other_Home.pdf",
        doc_type="other",
        summary="should not file",
    )
    assert result["status"] == "error"
    assert result.get("code") == "outside_inbox"
    assert outside.exists()


def test_read_document_allows_inbox_file(isolated_data):
    inbox = get_source_dir()
    path = write_minimal_png(inbox / "ok.png")
    result = filesystem.read_document(str(path))
    assert result["status"] == "success"
    assert Path(result["path"]).resolve() == path.resolve()


def test_require_inbox_source_blocks_symlink_escape(isolated_data):
    outside = write_minimal_pdf(isolated_data.parent / "linked.pdf")
    inbox = get_source_dir()
    link = inbox / "sneaky.pdf"
    try:
        link.symlink_to(outside)
    except OSError:
        # Some environments disallow symlinks; confinement still covers absolute paths.
        return
    result = filesystem.require_inbox_source(str(link))
    # Resolved target is outside inbox → reject.
    assert isinstance(result, dict)
    assert result.get("code") == "outside_inbox"
