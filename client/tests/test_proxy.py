"""Tests for the /start proxy translation layer (Pipecat Cloud mocked)."""

import pytest
from fastapi.testclient import TestClient

import proxy
from proxy import Settings, build_start_payload, create_app


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("PCC_AGENT_NAME", "my-agent")
    monkeypatch.setenv("PCC_PUBLIC_API_KEY", "pk_test")
    monkeypatch.setenv("MOQ_RELAY_URL", "https://relay.example.com/anon")
    monkeypatch.delenv("PCC_START_URL", raising=False)
    monkeypatch.delenv("ACCESS_KEY", raising=False)
    return Settings()


@pytest.fixture
def gated(settings, monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", "sesame")
    return Settings()


@pytest.fixture
def pcc(monkeypatch):
    """Capture the payload sent to Pipecat Cloud and return a canned response."""
    calls: list[dict] = []

    async def fake_pcc_start(_settings, payload):
        calls.append(payload)
        return {"sessionId": "sess-moq"}

    monkeypatch.setattr(proxy, "pcc_start", fake_pcc_start)
    return calls


# --- translation -----------------------------------------------------------


def test_settings_default_start_url(settings):
    assert settings.start_url == "https://api.pipecat.daily.co/v1/public/my-agent/start"


def test_moq_payload_mints_namespace_and_omits_transport(settings):
    transport, payload = build_start_payload({"transport": "moq"}, settings)
    assert transport == "moq"
    assert "transport" not in payload
    moq = payload["body"]["moq"]
    assert moq["namespace"].startswith("pipecat-")
    assert len(moq["namespace"]) == len("pipecat-") + 16
    assert moq["relayUrl"] == "https://relay.example.com/anon"
    assert moq["clientId"] == "request"
    assert moq["botId"] == "response"


def test_moq_namespace_is_unique_per_call(settings):
    ns = {
        build_start_payload({"transport": "moq"}, settings)[1]["body"]["moq"]["namespace"]
        for _ in range(5)
    }
    assert len(ns) == 5


def test_moq_honors_caller_namespace_and_body(settings):
    _, payload = build_start_payload(
        {"transport": "moq", "namespace": "my-room", "body": {"user_id": 7, "lang": "en"}},
        settings,
    )
    assert payload["body"]["user_id"] == 7
    assert payload["body"]["lang"] == "en"
    assert payload["body"]["moq"]["namespace"] == "my-room"


@pytest.mark.parametrize("transport", ["daily", "webrtc", "websocket", None])
def test_unsupported_transport_rejected(settings, transport):
    with pytest.raises(Exception) as exc:
        build_start_payload({"transport": transport} if transport else {}, settings)
    assert getattr(exc.value, "status_code", None) == 400


def test_validate_rejects_relay_url_without_path(monkeypatch):
    monkeypatch.setenv("PCC_AGENT_NAME", "a")
    monkeypatch.setenv("PCC_PUBLIC_API_KEY", "pk")
    monkeypatch.setenv("MOQ_RELAY_URL", "https://relay.example.com")
    with pytest.raises(RuntimeError, match="has no path"):
        Settings().validate()


def test_validate_accepts_relay_url_with_path(monkeypatch):
    monkeypatch.setenv("PCC_AGENT_NAME", "a")
    monkeypatch.setenv("PCC_PUBLIC_API_KEY", "pk")
    monkeypatch.setenv("MOQ_RELAY_URL", "https://relay.example.com/anon")
    Settings().validate()


# --- endpoints -------------------------------------------------------------


def test_start_endpoint_moq_returns_runner_shaped_config(settings, pcc):
    client = TestClient(create_app(settings))
    r = client.post("/start", json={"transport": "moq"})
    assert r.status_code == 200
    data = r.json()
    assert data["sessionId"] == "sess-moq"
    moq = data["moq"]
    sent = pcc[0]["body"]["moq"]
    assert moq["namespace"] == sent["namespace"]
    assert moq == {
        "relayUrl": "https://relay.example.com/anon",
        "certHash": None,
        "serve": False,
        "namespace": sent["namespace"],
        "clientId": "request",
        "botId": "response",
        "transcriptTrack": "transcript.json.z",
    }


def test_start_endpoint_rejects_other_transports(settings, pcc):
    client = TestClient(create_app(settings))
    r = client.post("/start", json={"transport": "websocket"})
    assert r.status_code == 400
    assert pcc == []  # never reached Pipecat Cloud


def test_healthz(settings):
    client = TestClient(create_app(settings))
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["relay"] == "https://relay.example.com/anon"  # shown in the UI header


def test_root_redirects_to_client(settings, pcc):
    client = TestClient(create_app(settings))
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/client/"


def test_ui_cache_headers(settings, pcc):
    client = TestClient(create_app(settings))
    r = client.get("/client/")
    if r.status_code == 200:  # only when a UI build is present
        assert r.headers["cache-control"] == "no-cache"
    r = client.get("/client/assets/does-not-exist.js")
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


# --- access key gate -------------------------------------------------------


def test_gate_disabled_when_unset(settings, pcc):
    client = TestClient(create_app(settings))
    assert client.post("/start", json={"transport": "moq"}).status_code == 200


def test_gate_blocks_without_key(gated, pcc):
    client = TestClient(create_app(gated))
    assert client.post("/start", json={"transport": "moq"}).status_code == 401
    assert client.get("/", follow_redirects=False).status_code == 401
    assert client.get("/healthz").status_code == 200  # always open
    assert pcc == []


def test_gate_accepts_header(gated, pcc):
    client = TestClient(create_app(gated))
    r = client.post("/start", json={"transport": "moq"}, headers={"X-Access-Key": "sesame"})
    assert r.status_code == 200


def test_gate_rejects_wrong_key(gated, pcc):
    client = TestClient(create_app(gated))
    r = client.post("/start", json={"transport": "moq"}, headers={"X-Access-Key": "nope"})
    assert r.status_code == 401
    assert client.get("/client/?key=nope", follow_redirects=False).status_code == 401


def test_gate_query_key_sets_cookie_and_redirects(gated, pcc):
    client = TestClient(create_app(gated))
    r = client.get("/client/?key=sesame", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith("/client/")
    assert "key=" not in r.headers["location"]
    assert client.cookies.get("access_key") == "sesame"
    # the cookie alone now authorizes the SPA's /start call
    assert client.post("/start", json={"transport": "moq"}).status_code == 200
