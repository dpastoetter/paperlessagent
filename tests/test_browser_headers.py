"""Browser security headers and privacy-safe static assets."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.security_headers import BROWSER_SECURITY_HEADERS, CONTENT_SECURITY_POLICY


def test_root_and_api_send_browser_hardening_headers(client):
    for path in ("/", "/api/health", "/static/styles.css"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        for name, value in BROWSER_SECURITY_HEADERS.items():
            assert resp.headers.get(name) == value, f"{path} missing {name}"


def test_csp_is_strict_and_blocks_third_party():
    csp = CONTENT_SECURITY_POLICY
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-src 'self' blob:" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "unsafe-inline" not in csp
    assert "fonts.googleapis" not in csp
    assert "https:" not in csp


def test_spa_has_no_external_fonts_or_inline_theme_script():
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    assert 'src="/static/theme-boot.js' in html
    assert 'localStorage.getItem("pa-theme")' not in html
    assert "<script>\n" not in html

    css = Path("app/static/styles.css").read_text(encoding="utf-8")
    assert "Space Grotesk" not in css
    assert "JetBrains Mono" not in css
    assert "ui-sans-serif" in css or "system-ui" in css


def test_theme_boot_script_is_served(client):
    resp = client.get("/static/theme-boot.js")
    assert resp.status_code == 200
    assert "pa-theme" in resp.text
    assert resp.headers.get("Content-Security-Policy")
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "no-referrer"


def test_error_responses_also_carry_headers(isolated_data, monkeypatch):
    from paperless_agent.local_security import generate_api_token
    from paperless_agent.sessions import clear_all_sessions

    monkeypatch.setenv("PAPERLESS_API_TOKEN", generate_api_token())
    clear_all_sessions()
    bare = TestClient(app)
    resp = bare.get("/api/inbox")
    assert resp.status_code == 401
    assert resp.headers.get("Content-Security-Policy") == CONTENT_SECURITY_POLICY
    assert resp.headers.get("X-Frame-Options") == "DENY"
