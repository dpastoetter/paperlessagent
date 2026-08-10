"""Cloud LLM processing disclaimer gates."""

from __future__ import annotations

from paperless_agent.privacy import (
    CLOUD_DISCLAIMER_VERSION,
    accept_cloud_disclaimer,
    clear_privacy_cache,
    is_cloud_disclaimer_accepted,
    revoke_cloud_disclaimer,
)


def test_disclaimer_defaults_to_not_accepted(isolated_data):
    clear_privacy_cache()
    assert is_cloud_disclaimer_accepted() is False


def test_accept_and_revoke_disclaimer(isolated_data):
    status = accept_cloud_disclaimer()
    assert status["accepted"] is True
    assert status["version"] == CLOUD_DISCLAIMER_VERSION
    assert status["accepted_at"]
    assert is_cloud_disclaimer_accepted() is True
    assert (isolated_data / "privacy.json").exists()

    revoked = revoke_cloud_disclaimer()
    assert revoked["accepted"] is False
    assert is_cloud_disclaimer_accepted() is False


def test_auth_routes_require_disclaimer(client):
    denied = client.post("/api/auth/openai/start")
    assert denied.status_code == 403
    assert "disclaimer" in denied.json()["detail"].lower()

    denied_key = client.post("/api/auth/api-key", json={"api_key": "sk-test-key"})
    assert denied_key.status_code == 403

    denied_provider = client.post("/api/llm/provider", json={"provider": "openai"})
    assert denied_provider.status_code == 403

    # Ollama remains available without the cloud disclaimer.
    ok_ollama = client.post("/api/ollama/enable", json={})
    assert ok_ollama.status_code == 200

    accept = client.post("/api/privacy/cloud-disclaimer", json={"accepted": True})
    assert accept.status_code == 200
    assert accept.json()["cloud_disclaimer"]["accepted"] is True

    # After approval, provider switch is allowed (auth start may still fail later
    # without a real OAuth listener — but must not be blocked by disclaimer).
    provider = client.post("/api/llm/provider", json={"provider": "openai"})
    assert provider.status_code == 200
    assert provider.json()["applied"]["provider"] == "openai"


def test_privacy_status_endpoint(client):
    before = client.get("/api/privacy/cloud-disclaimer").json()
    assert before["cloud_disclaimer"]["accepted"] is False

    client.post("/api/privacy/cloud-disclaimer", json={"accepted": True})
    after = client.get("/api/privacy/cloud-disclaimer").json()
    assert after["cloud_disclaimer"]["accepted"] is True

    client.post("/api/privacy/cloud-disclaimer", json={"accepted": False})
    revoked = client.get("/api/privacy/cloud-disclaimer").json()
    assert revoked["cloud_disclaimer"]["accepted"] is False
