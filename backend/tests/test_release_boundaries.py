import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app, base_url="http://127.0.0.1:8000")


@pytest.mark.parametrize("method,path", [
    ("post", "/api/jobs"), ("put", "/api/profile/preferences"),
    ("post", "/api/discovery/sources"),
])
@pytest.mark.parametrize("headers", [
    {"Host": "foreign.example"}, {"Origin": "https://foreign.example"},
    {"Host": "localhost:invalid"}, {"Sec-Fetch-Site": "cross-site"},
    [("Host", "localhost"), ("Host", "example.invalid")],
])
def test_private_validation_rejects_foreign_browser(method, path, headers):
    assert client.request(method, path, headers=headers, json=[]).status_code == 403


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "http://127.0.0.1:8000"},
    {"Origin": "http://localhost:5173", "Sec-Fetch-Site": "same-origin"},
])
def test_local_requests_reach_job_validation(headers):
    assert client.post("/api/jobs", headers=headers, json=[]).status_code == 422

def test_extension_rejects_duplicate_origins():
    from assistant_routes import EXTENSION_ORIGIN

    response = client.options("/api/assistant/extension/preview", headers=[
        ("Origin", EXTENSION_ORIGIN), ("Origin", "https://foreign.example"),
        ("Access-Control-Request-Method", "POST"),
    ])
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers
