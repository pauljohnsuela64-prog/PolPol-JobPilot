import pytest

import ai_settings as ai


@pytest.mark.parametrize("value", [
    "http://localhost:11434", "https://127.0.0.1", "http://127.0.0.1@evil.example",
    "http://127.0.0.1/proxy", "http://[::1%25eth0]", "http://127.1",
    "http://127.0.0.1?host=example.org", "http://0.0.0.0",
])
def test_requires_numeric_http_loopback(value):
    with pytest.raises(ValueError):
        ai.Settings(endpoint=value)


@pytest.mark.parametrize("value", ["http://127.0.0.1:11434", "http://[::1]:11434"])
def test_loopback_origins(value):
    assert ai.Settings(endpoint=value + "/").endpoint == value


@pytest.mark.parametrize("metadata", [
    {"remote_host": "https://example.org", "details": {"format": "gguf"}},
    {"remote_model": "plain", "details": {"format": "gguf"}},
    {"details": {}},
])
def test_disguised_remote_model_never_receives_prompt(monkeypatch, metadata):
    monkeypatch.setattr(ai, "load_settings", lambda: ai.Settings(enabled=True, model="local"))
    calls = []

    def request(endpoint, path, payload=None, **kwargs):
        calls.append(path)
        return {"models": [{"name": "local"}]} if path == "/api/tags" else metadata

    monkeypatch.setattr(ai, "request_json", request)
    with pytest.raises(ValueError, match="could not be verified as local"):
        ai.infer({"private": "resume"})
    assert calls == ["/api/tags", "/api/show"]


def test_disabled_never_sends_prompt(monkeypatch):
    monkeypatch.setattr(ai, "load_settings", ai.Settings)
    monkeypatch.setattr(ai, "request_json", lambda *args, **kw: pytest.fail("unexpected network"))
    with pytest.raises(ValueError, match="disabled"):
        ai.infer({})


def test_refuses_cloud_alias():
    with pytest.raises(ValueError):
        ai.Settings(model="qwen:cloud")


def test_redirects_and_proxies_are_disabled(monkeypatch):
    import httpx

    calls = []
    original_client = httpx.Client

    def redirect(request):
        calls.append(str(request.url))
        return httpx.Response(307, headers={"Location": "https://example.org"})

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original_client(transport=httpx.MockTransport(redirect), **kwargs)

    monkeypatch.setattr(ai.httpx, "Client", client)
    with pytest.raises(httpx.HTTPStatusError):
        ai.request_json(ai.DEFAULT_ENDPOINT, "/api/tags")
    assert calls == [ai.DEFAULT_ENDPOINT + "/api/tags"]


def test_first_get_never_probes_default(monkeypatch):
    monkeypatch.setattr(ai, "load_settings", lambda: None)
    monkeypatch.setattr(ai, "request_json", lambda *a, **kw: pytest.fail("unexpected network"))
    settings = ai.get_settings()
    assert settings["status"] == "disabled" and settings["models"] == []


@pytest.mark.parametrize(("exc", "message"), [
    (ai.httpx.ConnectError("secret"), "unreachable"),
    (ai.httpx.ReadTimeout("secret"), "timed out"),
])
def test_status_errors_do_not_expose_upstream_text(monkeypatch, exc, message):
    def failed(*args):
        raise exc

    monkeypatch.setattr(ai, "installed", failed)
    result = ai.describe(ai.Settings(enabled=True, model="local"))
    assert message in result["error"] and "secret" not in result["error"]


def test_missing_model_reports_installed_choices(monkeypatch):
    monkeypatch.setattr(ai, "installed", lambda endpoint: ["other"])
    result = ai.describe(ai.Settings(enabled=True, model="missing"))
    assert result["models"] == ["other"]
    assert "missing" in result["error"]


def test_generation_uses_bounded_context_and_no_thinking(monkeypatch):
    monkeypatch.setattr(ai, "load_settings", lambda: ai.Settings(enabled=True, model="qwen3.5:4b"))
    def request(endpoint, path, payload=None, **kwargs):
        if path == "/api/tags":
            return {"models": [{"name": "qwen3.5:4b"}]}
        if path == "/api/show":
            assert payload["verbose"] is False
            assert kwargs["limit"] > 1024 * 1024
            return {"details": {"format": "gguf"}}
        assert path == "/api/generate"
        assert payload["think"] is False
        assert payload["options"]["num_ctx"] == 8192
        return {"done": True, "response": '{"fact_ids":[]}'}
    monkeypatch.setattr(ai, "request_json", request)
    assert ai.infer({}) == {"fact_ids": []}


def test_settings_require_local_origin_and_host():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(ai.router)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/ai/settings", headers={"Host": "evil.example"}).status_code == 403
    assert client.put("/ai/settings", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
