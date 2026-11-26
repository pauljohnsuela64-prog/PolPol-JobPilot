"""Exercise the Ready queue and companion handoff using synthetic applicant facts."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from assistant_routes import EXTENSION_ORIGIN, TARGET_URL, package_fields
from discovery_routes import store_listings
from job_providers import Posting
from main import app
from test_discovery import migration_database


client = TestClient(app, base_url="http://127.0.0.1:8000")
origin = {"Origin": EXTENSION_ORIGIN}
extension = "/api/assistant/extension/"
jobs = "/api/assistant/jobs"
master = {"name": "Example Person", "contact": ["example@example.org"], "skills": ["SQL"],
          "education": ["Example College"], "experience": ["Prepared SQL reports."]}


@pytest.fixture
def ready(migration_database):
    backend = Path(__file__).resolve().parents[1]
    with psycopg.connect(migration_database, autocommit=True) as db:
        for script in [backend / "schema.sql", *sorted((backend / "migrations").glob("*.sql"))]:
            db.execute(script.read_text())
        source = client.post("/api/discovery/sources", json={"company": "Demo", "provider": "lever", "board": "demo"}).json()
        store_listings(source, [Posting(external_id="demo", title="Engineer", company="Demo",
                                       url="https://example.org/jobs", description="Required: SQL.")])
        listing = client.get("/api/builder/jobs").json()[0]["id"]
        client.put("/api/builder/master", json=master).raise_for_status()
        resume = client.post(f"/api/builder/jobs/{listing}/drafts").json()["id"]
        packet = client.post(f"/api/preparation/jobs/{listing}/drafts", json={"resume_id": resume, "questions": [
            "What are your relevant skills?", "Expected salary?"]}).json()["id"]
        client.post(f"/api/preparation/drafts/{packet}/approve", json={"revision": 1}).raise_for_status()
        assert client.get(jobs).json() == []
        client.put(f"/api/builder/drafts/{resume}/approval", json={"approved": True, "revision": 1}).raise_for_status()
        db.execute((backend / "migrations/010_application_assistant.sql").read_text())
        yield f"{jobs}/{listing}", resume, packet, db


def prepare(path):
    packet = client.get(path + "/package").json()
    review = {"revision_key": packet["revision_key"], "reviewed": True}
    issued = client.post(path + "/handoff", json=review)
    issued.raise_for_status()
    return {"token": issued.json()["token"], "target_url": TARGET_URL}


def test_origin_and_host_boundaries():
    for headers in [{}, {"Origin": "https://example.org"}, {"Origin": "null"},
                    {**origin, "Host": "localhost:8000"}, {"Origin": "chrome-extension://" + "a"*32}]:
        response = client.post(extension + "preview", headers=headers,
                               json={"token": "A"*43, "target_url": TARGET_URL})
        assert response.status_code == 403 and "access-control-allow-origin" not in response.headers
        assert response.headers["cache-control"] == "no-store"
    assert client.get(jobs, headers=origin).status_code == 403
    headers = {**origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    allowed = client.options(extension + "preview", headers=headers)
    assert allowed.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    assert "access-control-allow-credentials" not in allowed.headers
    assert client.options(extension + "preview", headers={**headers, "Access-Control-Request-Headers": "authorization"}).status_code == 403


def test_removed_or_ambiguous_facts_do_not_fill():
    facts = {**master, "contact": ["a@example.org", "b@example.org"]}
    fields = package_fields(facts, "Example Person\na@example.org\nb@example.org", {"cover_letter": "Reviewed", "answers": []})
    by_key = {f["key"]: f for f in fields}
    for key in ("email", "phone", "experience", "education"):
        assert by_key[key]["status"] == "unsupported" and by_key[key]["value"] == "Needs your input."


def test_review_claim_completion_and_duplicate_protection(ready):
    path, _, _, db = ready
    packet = client.get(path + "/package").json()
    fields = {f["key"]: f for f in packet["fields"]}
    assert fields["screening_1"]["status"] == "reviewed" and fields["screening_2"]["status"] == "unsupported"
    assert client.post(path + "/handoff", json={"reviewed": False, "revision_key": packet["revision_key"]}).status_code == 422
    request = prepare(path)
    assert client.post(extension + "preview", headers=origin, json={**request, "target_url": TARGET_URL + "?x"}).status_code == 422
    preview = client.post(extension + "preview", headers=origin, json=request).json()
    assert {"screening_2", "cover_letter"}.isdisjoint(f["key"] for f in preview["fields"])
    for keys in (["phone"], ["email", "email"], ["screening_2"], ["cover_letter"]):
        assert client.post(extension + "claim", headers=origin, json={**request, "field_keys": keys}).status_code == 409
    def claim(_):
        return client.post(extension + "claim", headers=origin, json={**request, "field_keys": ["email", "screening_1"]})
    with ThreadPoolExecutor(2) as pool:
        attempts = list(pool.map(claim, range(2)))
    assert sorted(r.status_code for r in attempts) == [200, 409]
    result = next(r.json() for r in attempts if r.is_success)
    complete = {"receipt": result["receipt"], "filled_keys": ["email"]}
    assert client.post(extension + "complete", headers=origin, json={**complete, "filled_keys": ["education"]}).status_code == 409
    for _ in range(2):
        client.post(extension + "complete", headers=origin, json=complete).raise_for_status()
    assert client.post(extension + "complete", headers=origin, json={**complete, "filled_keys": []}).status_code == 409
    log = client.get(path + "/package").json()["activity"]
    assert [e["event"] for e in log] == ["filled", "fill_authorized", "prepared"]
    assert all(set(e) == {"event", "created_at", "field_count"} for e in log)
    assert db.execute("SELECT count(*) FROM application_queue_history").fetchone() == (0,)
    row = client.get("/api/queue").json()[0]
    assert row["state"] == "ready" and row["job_id"] is None
    client.post(f"/api/queue/{row['key']}/submit", json={"confirmed": True, "channel": "Synthetic test"}).raise_for_status()
    assert client.get(jobs).json() == [] and client.get(path + "/package").status_code == 409
    assert client.get(path + "/activity").json() == log


def test_stale_approvals_expiry_and_replacement(ready):
    path, resume, _, db = ready
    first, second = prepare(path), prepare(path)
    assert client.post(extension + "preview", headers=origin, json=first).status_code == 409
    db.execute("UPDATE assistant_handoffs SET expires_at=now()")
    assert client.post(extension + "preview", headers=origin, json=second).status_code == 409
    third = prepare(path)
    client.put("/api/builder/master", json={**master, "education": []}).raise_for_status()
    assert client.post(extension + "claim", headers=origin, json={**third, "field_keys": ["email"]}).status_code == 409
    fresh = prepare(path)
    client.put(f"/api/builder/drafts/{resume}/approval", json={"approved": False, "revision": 1}).raise_for_status()
    assert client.post(extension + "claim", headers=origin, json={**fresh, "field_keys": ["email"]}).status_code == 409
