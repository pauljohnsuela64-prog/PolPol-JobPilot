"""Exercise Phase 6 through the same local endpoints used by React."""
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient

from discovery_routes import store_listings
from job_providers import Posting
from main import app
from test_discovery import migration_database


def test_prepare_review_reopen_and_export(migration_database):
    root = Path(__file__).resolve().parents[1]
    client = TestClient(app, base_url="http://localhost")
    api = "/api/preparation"
    with psycopg.connect(migration_database, autocommit=True) as db:
        for file in [root / "schema.sql", *sorted((root / "migrations").glob("00[235]*.sql"))]:
            db.execute(file.read_text())
        tracked = client.post("/api/jobs", json={"title": "Existing", "company": "Fixture"}).json()
        source = client.post("/api/discovery/sources", json={
            "company": "Fixture", "board": "fixture", "provider": "lever"}).json()
        opening = Posting(external_id="fixture", title="Developer", company="Fixture",
                          description="Required: Python and Java.", url="https://example.com/jobs/1")
        store_listings(source, [opening, opening.model_copy(update={"external_id": "other"})])
        master = {"name": "Sample Applicant", "skills": ["Python"]}
        client.put("/api/builder/master", json=master).raise_for_status()
        first, other = client.get("/api/builder/jobs").json()
        resume = client.post(f"/api/builder/jobs/{first['id']}/drafts").json()["id"]
        migration = (root / "migrations/006_application_preparation.sql").read_text()
        db.execute(migration)
        path = f"{api}/jobs/{first['id']}/drafts"
        assert client.post(f"{api}/jobs/{other['id']}/drafts", json={"resume_id": resume}).status_code == 422
        assert client.post(path, json={}, headers={"Origin": "https://elsewhere.example"}).status_code == 403
        draft = client.post(path, json={"resume_id": resume, "questions": ["What salary do you expect?"]}).json()
        second = client.post(path, json={}).json()
        db.execute(migration)
        assert [v["id"] for v in client.get(path).json()] == [second["id"], draft["id"]]
        assert draft["answers"][0]["answer"] == "Needs your input."
        assert "Python" in draft["cover_letter"] and "Java" not in draft["cover_letter"]
        record = f"{api}/drafts/{draft['id']}"
        client.delete(f"/api/builder/drafts/{resume}").raise_for_status()
        assert client.get(record).json()["resume_snapshot"]["id"] == resume
        store_listings(source, [opening.model_copy(update={"external_id": "other", "description": "Changed."})])
        assert client.get(record).json()["job_snapshot"]["description"] == opening.description
        export = record + "/export"
        assert client.post(export + "/cover_letter/pdf", json={"revision": 1}).status_code == 409
        edit = {k: draft[k] for k in ("cover_letter", "answers", "checklist", "revision")}
        edit["answers"][0]["answer"] = "I would like to discuss compensation."
        for item in edit["checklist"]:
            item["done"] = True
        saved = client.put(record, json=edit).json()
        assert saved["revision"] == 2 and saved["approved_revision"] is None
        assert client.put(record, json=edit).status_code == 409
        assert client.post(record + "/approve", json={"revision": 1}).status_code == 409
        assert client.post(record + "/approve", json={"revision": 2}).json()["approved_revision"] == 2
        for artifact, fmt in [("cover_letter", "docx"), ("cover_letter", "pdf"), ("answers", "txt"), ("checklist", "txt")]:
            download = client.post(f"{export}/{artifact}/{fmt}", json={"revision": 2})
            assert download.status_code == 200 and download.content
            assert "attachment" in download.headers["Content-Disposition"]
        edit.update(revision=2, cover_letter="Updated by applicant.")
        assert client.put(record, json=edit).json()["approved_revision"] is None
        for revision in (2, 3):
            assert client.post(export + "/answers/txt", json={"revision": revision}).status_code == 409
        assert client.delete(record).status_code == 204
        assert client.get(record).status_code == 404
        assert client.get(f"{api}/drafts/{second['id']}").json() == second
        assert client.get("/api/jobs").json() == [tracked]
        assert client.get("/api/builder/master").json()["skills"] == master["skills"]
