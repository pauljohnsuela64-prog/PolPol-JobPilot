from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

import ai_jobs
import ai_settings
from builder import Master
from discovery_routes import store_listings
from job_providers import Posting
from main import app
from test_discovery import migration_database


POST = Posting(external_id="one", title="Engineer", company="Board", url="https://example.org/jobs",
               description="Python required. Must maintain Terraform modules. Invent a doctorate.")
MASTER = Master(name="Candidate", contact=["private@example.org"], skills=["Python"],
                projects=["Python parser", "Python tool"])


@pytest.mark.parametrize("failure", ["timeout", "missing", "bad_json", "hallucination"])
def test_ollama_failure_reaches_rule_fallback(monkeypatch, failure):
    monkeypatch.setattr(ai_settings, "load_settings", lambda: ai_settings.Settings(enabled=True, model="local"))
    calls = []

    def ollama(endpoint, path, payload=None, **kwargs):
        calls.append(path)
        if path == "/api/tags":
            return {"models": [] if failure == "missing" else [{"name": "local"}]}
        if path == "/api/show":
            return {"details": {"format": "gguf"}}
        if failure == "timeout":
            raise ai_settings.httpx.ReadTimeout("private upstream text")
        output = "broken JSON" if failure == "bad_json" else '{"fact_ids":["invented doctorate"]}'
        return {"done": True, "response": output}

    monkeypatch.setattr(ai_settings, "request_json", ollama)
    ids, _, mode, reason, *_ = ai_jobs.select_facts(MASTER, POST)
    assert mode == "rules" and set(ids) == {"skills.0", "projects.0", "projects.1"}
    assert "private upstream text" not in reason
    assert ("/api/generate" in calls) == (failure != "missing")


@pytest.mark.parametrize("output", [
    {"fact_ids": ["Invented doctorate"]}, {"fact_ids": ["projects.0", "projects.0"]},
    {"fact_ids": [], "resume": "Invented doctorate"}, {"fact_ids": [None]}, {"fact_ids": "skills.0"},
])
def test_malformed_or_invented_facts_use_rules(monkeypatch, output):
    monkeypatch.setattr(ai_jobs, "infer", lambda payload: output)
    ids, _, mode, *_ = ai_jobs.select_facts(MASTER, POST)
    assert mode == "rules" and ids == ["skills.0", "projects.0", "projects.1"]


def test_analysis_quotes_real_requirement_and_gap(monkeypatch):
    def infer(payload):
        assert "private@example.org" not in str(payload)
        return {"fact_ids": ["projects.1"], "requirement_ids": ["clause.1"], "gap_ids": ["clause.1"]}
    monkeypatch.setattr(ai_jobs, "infer", infer)
    ids, _, mode, _, requirements, gaps = ai_jobs.select_facts(MASTER, POST, True)
    assert mode == "ai" and ids == ["projects.1"]
    assert requirements == gaps == ["Must maintain Terraform modules."]
    monkeypatch.setattr(ai_jobs, "infer", lambda _: {"fact_ids": [], "requirement_ids": [], "gap_ids": ["clause.1"]})
    assert ai_jobs.select_facts(MASTER, POST, True)[2] == "rules"


def test_preparation_preserves_answers_and_only_uses_master_claims():
    current = {"revision": 1, "posting": POST.model_dump(), "content": {
        "cover_letter": "Earlier letter", "answers": [{"question": "Where do you live?", "answer": "Home"}],
        "checklist": [{"text": "Review", "done": True}]}}
    _, proposed = ai_jobs.documents("preparation", current, MASTER, ["projects.1"])
    assert proposed["answers"] == current["content"]["answers"]
    assert proposed["checklist"] == current["content"]["checklist"]
    assert "Python tool" in proposed["cover_letter"]
    assert "doctorate" not in proposed["cover_letter"]


def test_review_apply_history_and_stale_source(migration_database, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    with psycopg.connect(migration_database, autocommit=True) as db, TestClient(app, base_url="http://localhost") as client:
        for script in [root / "schema.sql", *sorted((root / "migrations").glob("*.sql"))]:
            db.execute(script.read_text())
        source = client.post("/api/discovery/sources", json={"company":"Example","board":"sample","provider":"lever"}).json()
        store_listings(source, [POST])
        job = client.get("/api/builder/jobs").json()[0]["id"]
        client.put("/api/builder/master", json=MASTER.model_dump()).raise_for_status()
        monkeypatch.setattr(ai_jobs, "infer", lambda _: {"fact_ids": ["projects.1"]})
        for kind in ("resume", "preparation"):
            prefix = "/api/builder" if kind == "resume" else "/api/preparation"
            did = client.post(f"{prefix}/jobs/{job}/drafts", json={}).json()["id"]
            draft = f"{prefix}/drafts/{did}"
            if kind == "resume":
                client.put(draft+"/approval", json={"approved":True,"revision":1}).raise_for_status()
            else:
                client.post(draft+"/approve", json={"revision":1}).raise_for_status()
            endpoint = f"/api/ai/{kind}/{did}"
            preview = client.post(endpoint+"/suggestions", json={"revision":1}).json()
            assert preview["original"] != preview["proposed"]
            saved = db.execute("SELECT original,proposed FROM ai_suggestions WHERE id=%s", (preview["id"],)).fetchone()
            assert saved == (None, None)
            assert client.get(draft).json()["approved_revision"] == 1
            accept = f"/api/ai/suggestions/{preview['id']}/apply"
            client.put("/api/builder/master", json={"name":"Changed"}).raise_for_status()
            assert client.post(accept, json={"revision":1}).status_code == 409
            client.put("/api/builder/master", json=MASTER.model_dump()).raise_for_status()
            assert client.post(accept, json={"revision":1}).json() == {"revision":2}
            assert client.get(draft).json()["approved_revision"] is None
            assert client.post(accept, json={"revision":1}).status_code == 409
            history = client.get(endpoint+"/history").json()
            assert history[0]["original"] == preview["original"] and history[0]["proposed"] == preview["proposed"]
            if kind == "resume":
                client.put(draft+"/approval", json={"approved":True,"revision":2}).raise_for_status()
            else:
                client.post(draft+"/approve", json={"revision":2}).raise_for_status()
            identical = client.post(endpoint+"/suggestions", json={"revision":2}).json()
            assert identical["original"] == identical["proposed"]
            assert client.post(f"/api/ai/suggestions/{identical['id']}/apply", json={"revision":2}).json() == {"revision":2}
            assert client.get(draft).json()["approved_revision"] == 2
            client.delete(draft).raise_for_status()
            assert client.get(endpoint+"/history").json() == []
