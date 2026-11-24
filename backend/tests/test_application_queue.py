from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient

from discovery_routes import store_listings
from job_providers import Posting
from main import app
from test_discovery import migration_database


def test_queue_lifecycle_and_legacy_data(migration_database):
    root = Path(__file__).resolve().parents[1]
    c = TestClient(app, base_url="http://localhost")
    queue = "/api/queue"

    def rows():
        r = c.get(queue)
        r.raise_for_status()
        return r.json()

    def listing():
        return next(r for r in rows() if r["listing_id"] == opening_id)

    with psycopg.connect(migration_database, autocommit=True) as db:
        for f in [root / "schema.sql", *sorted((root / "migrations").glob("00[2356]*.sql"))]:
            db.execute(f.read_text())
        manual = c.post("/api/jobs", json={"title": "Legacy", "company": "Demo"}).json()["job_id"]
        c.patch(f"/api/jobs/{manual}", json={"status": "interview"}).raise_for_status()
        source = c.post("/api/discovery/sources", json={
            "provider": "lever", "board": "demo", "company": "Demo"}).json()
        post = Posting(external_id="persistent-id", title="Engineer", company="Demo",
                       url="https://jobs.lever.co/demo/123", description="Required: Python")
        store_listings(source, [post, post.model_copy(update={"external_id": "unsafe", "url": "javascript:alert(1)"})])
        opening_id = db.execute("SELECT id FROM discovered_jobs WHERE external_id=%s", (post.external_id,)).fetchone()[0]
        c.put("/api/builder/master", json={"name": "Example", "skills": ["Python"]}).raise_for_status()
        draft = c.post(f"/api/builder/jobs/{opening_id}/drafts").json()["id"]
        resume = f"/api/builder/drafts/{draft}"
        prep = f"/api/preparation/jobs/{opening_id}/drafts"
        packet = c.post(prep, json={"resume_id": draft}).json()
        packet_url = f"/api/preparation/drafts/{packet['id']}"
        c.post(packet_url + "/approve", json={"revision": 1}).raise_for_status()
        dashboard = c.get("/api/jobs").json()
        migration = (root / "migrations/007_application_queue.sql").read_text()
        db.execute(migration)
        assert c.get("/api/jobs").json() == dashboard
        assert c.get(packet_url).json()["approved_revision"] == 1
        assert listing()["state"] == "draft" and listing()["url"] == post.url
        assert not next(r for r in rows() if r["listing_id"] and r["listing_id"] != opening_id)["url"]
        assert c.get(queue, headers={"origin": "https://untrusted.example"}).status_code == 403
        c.put(resume + "/approval", json={"approved": True, "revision": 1}).raise_for_status()
        assert listing()["state"] == "ready"
        c.put(resume + "/approval", json={"approved": False, "revision": 1}).raise_for_status()
        assert listing()["state"] == "draft"
        c.put(resume + "/approval", json={"approved": True, "revision": 1}).raise_for_status()
        c.put(resume, json={"content": "Changed by applicant", "revision": 1}).raise_for_status()
        assert listing()["state"] == "draft"
        c.put(resume + "/approval", json={"approved": True, "revision": 2}).raise_for_status()
        assert listing()["state"] == "draft"  # The packet still contains revision 1.
        new_packet = c.post(prep, json={"resume_id": draft}).json()
        assert listing()["state"] == "draft"
        c.post(f"/api/preparation/drafts/{new_packet['id']}/approve", json={"revision": 1}).raise_for_status()
        assert listing()["state"] == "ready"
        edited_packet = {k: new_packet[k] for k in ("cover_letter", "answers", "checklist", "revision")}
        edited_packet["cover_letter"] = "Edited letter"
        new_url = f"/api/preparation/drafts/{new_packet['id']}"
        c.put(new_url, json=edited_packet).raise_for_status()
        assert listing()["state"] == "draft" and not listing()["prep_approved"]
        c.post(new_url + "/approve", json={"revision": 2}).raise_for_status()
        key = listing()["key"]
        submit = f"{queue}/{key}/submit"
        details = {"confirmed": True, "channel": "Employer website", "submitted_at": "2025-01-12T13:30:00+08:00",
                   "reference": "demo-1", "note": "Synthetic submission"}
        for invalid in [dict(details, confirmed=False), {k:v for k,v in details.items() if k != "confirmed"},
                        dict(details, submitted_at="2099-05-01T01:00:00Z"),
                        dict(details, channel="invalid\x00channel"), dict(details, url="https://example.com/override")]:
            assert c.post(submit, json=invalid).status_code == 422
        assert c.get("/api/jobs").json() == dashboard
        # Two windows can confirm simultaneously. Only one may create a submission.
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: c.post(submit, json=details).status_code, range(2)))
        assert sum(200 <= code < 300 for code in results) == 1 and 409 in results
        store_listings(source, [post])
        db.execute(migration)
        row = listing()
        assert row["state"] == "submitted" and len(row["history"]) == 1
        assert c.post(f"{queue}/{row['key']}/submit", json=details).status_code == 409
        assert c.post(f"{queue}/job-{row['job_id']}/submit", json=details).status_code == 409
        similar = next(r for r in rows() if r["listing_id"] and r["listing_id"] != opening_id)
        assert similar["duplicate_warning"] and not similar["job_id"]
        assert c.post(f"{queue}/{similar['key']}/submit", json=details).status_code == 409
        tracked = c.get("/api/jobs").json()
        assert len(tracked) == 2 and next(j for j in tracked if j["job_id"] == row["job_id"])["status"] == "applied"
        correction = dict(details, expected_event_id=row["history"][0]["id"], note="Corrected reference",
                          reference="demo-2", follow_up_on="2025-01-20")
        correct = f"{queue}/{row['key']}/correct"
        c.post(correct, json=correction).raise_for_status()
        assert c.post(correct, json=correction).status_code == 409
        row = listing()
        assert row["state"] == "follow_up"
        assert [e["reference"] for e in row["history"]] == ["demo-2", "demo-1"]
        assert c.get("/api/jobs").json() == tracked
        legacy = next(r for r in rows() if r["job_id"] == manual)
        assert legacy["history_unknown"] and not legacy["url"]
        legacy_submit = f"{queue}/{legacy['key']}/submit"
        assert c.post(legacy_submit, json=details).status_code == 409
        c.post(legacy_submit, json=dict(details, acknowledge_history=True)).raise_for_status()
        assert c.get("/api/jobs").json() == tracked
