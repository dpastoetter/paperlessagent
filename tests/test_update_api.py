"""API-level tests for the GitHub self-update endpoints (no network)."""

from __future__ import annotations

from paperless_agent.updater import get_current_version


def test_update_status_local_only(client):
    """Without check=true the endpoint must not hit the network."""
    resp = client.get("/api/update/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["current_version"] == get_current_version()
    assert "/" in body["repo"]
    assert "update_available" not in body  # no GitHub check performed


def test_update_status_check_reports_newer(client, monkeypatch):
    monkeypatch.setattr(
        "paperless_agent.updater._fetch_latest_release",
        lambda: {
            "tag": "v99.0.0",
            "name": "v99.0.0",
            "notes": "big release",
            "published_at": "2026-01-01T00:00:00Z",
            "html_url": "https://github.com/x/y/releases/tag/v99.0.0",
            "tarball_url": "https://api.github.com/repos/x/y/tarball/v99.0.0",
            "verifiable": True,
            "verification_error": None,
            "artifact": {
                "filename": "paperlessagent-99.0.0.tar.gz",
                "download_url": "https://example.invalid/paperlessagent-99.0.0.tar.gz",
                "expected_sha256": "a" * 64,
            },
        },
    )
    body = client.get("/api/update/status?check=true").json()
    assert body["update_available"] is True
    assert body["latest_version"] == "99.0.0"
    assert body["notes"] == "big release"
    assert body["verifiable"] is True
    assert body["expected_sha256"] == "a" * 64


def test_update_status_check_handles_no_releases(client, monkeypatch):
    monkeypatch.setattr(
        "paperless_agent.updater._fetch_latest_release", lambda: None
    )
    body = client.get("/api/update/status?check=true").json()
    assert body["status"] == "success"
    assert body["update_available"] is False
    assert "No releases" in body["message"]


def test_update_apply_conflicts_when_up_to_date(client, monkeypatch):
    monkeypatch.setattr(
        "paperless_agent.updater._fetch_latest_release",
        lambda: {
            "tag": f"v{get_current_version()}",
            "name": "current",
            "notes": "",
            "published_at": None,
            "html_url": "",
            "tarball_url": "https://api.github.com/repos/x/y/tarball/current",
        },
    )
    resp = client.post("/api/update/apply")
    assert resp.status_code == 409
    assert "up to date" in resp.json()["detail"].lower()


def test_update_restart_endpoint_schedules(client, monkeypatch):
    """Restart must not actually exec during tests."""
    calls: list[bool] = []
    monkeypatch.setattr(
        "app.main.schedule_restart",
        lambda: calls.append(True) or {"status": "success", "message": "Restarting..."},
    )
    resp = client.post("/api/update/restart")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert calls == [True]
