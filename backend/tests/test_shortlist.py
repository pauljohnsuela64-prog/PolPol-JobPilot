from pathlib import Path

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient

import shortlist
from database import database
from discovery_routes import store_listings
from job_providers import Posting
from test_discovery import migration_database


app = FastAPI()
app.include_router(shortlist.router)
client = TestClient(app, base_url="http://localhost")
base = "/api/shortlist"


def test_local_request_and_threshold_validation():
    assert client.get(base, headers={"origin": "https://external.example"}).status_code == 403
    assert client.put(base + "/settings", json={"min_score": 101, "min_confidence": 0}).status_code == 422


def test_existing_and_refreshed_matches(migration_database):
    root = Path(__file__).resolve().parents[1]
    with psycopg.connect(migration_database, autocommit=True) as db:
        for name in ("schema.sql", "migrations/002_profile.sql", "migrations/003_discovery.sql"):
            db.execute((root / name).read_text())
        source_id = db.execute("""
            INSERT INTO career_boards(provider,board,company) VALUES ('lever','demo','Demo') RETURNING id
        """).fetchone()[0]
        source = {"id": source_id, "provider": "lever"}
        post = Posting(external_id="one", title="Engineer", company="Demo", arrangement="remote",
                       url="https://jobs.lever.co/demo/one")
        store_listings(source, [post, post.model_copy(update={"external_id": "two", "title": "Designer"})])
        migration = (root / "migrations/008_shortlist.sql").read_text()
        db.execute(migration)
        db.execute(migration)
        assert client.get(base).json()["jobs"] == []
        db.execute("""UPDATE profile SET preferences='{"job_titles":["Engineer"]}' WHERE id=1""")
        assert client.get(base + "/settings").json() == {"min_score": 70, "min_confidence": 0}
        job, = client.get(base).json()["jobs"]
        assert job["newly_discovered"]
        assert job["compatibility"]["score"] == 100 and job["compatibility"]["confidence"] == 25
        path = base + f"/jobs/{job['id']}"
        client.patch(path, json={"reviewed": True}).raise_for_status()
        for filters in ({"reviewed": False}, {"arrangement": "hybrid"}, {"provider": "greenhouse"},
                        {"source_id": source_id + 1}, {"q": "Designer"}, {"offset": 1}):
            assert client.get(base, params=filters).json()["jobs"] == []
        for sort in ("best", "score", "confidence", "newest", "recent"):
            assert client.get(base, params={"sort": sort}).json()["jobs"][0]["id"] == job["id"]
        client.put(base + "/settings", json={"min_score": 100, "min_confidence": 26}).raise_for_status()
        assert client.get(base).json()["jobs"] == []
        client.put(base + "/settings", json={"min_score": 100, "min_confidence": 25}).raise_for_status()
        store_listings(source, [post])
        with database() as conn:
            shortlist.after_refresh(conn, [])
            shortlist.after_refresh(conn, [{"source_id": source_id, "company": "Demo", "error": "Feed unavailable."}])
        activity = client.get(base + "/activity").json()
        db.execute(migration)
        assert client.get(base + "/activity").json() == activity
        assert client.get(base + "/settings").json() == {"min_score": 100, "min_confidence": 25}
        assert [event["kind"] for event in activity] == ["refresh_failed", "new_match"]
        assert activity[1]["listing_id"] == job["id"]
        reviewed, = client.get(base, params={"reviewed": True}).json()["jobs"]
        assert reviewed["id"] == job["id"] and not reviewed["newly_discovered"]
        client.patch(path, json={"reviewed": False}).raise_for_status()
        assert client.get(base, params={"reviewed": True}).json()["jobs"] == []

        # Include zero-score jobs to exercise ranking independently of date order.
        client.put(base + "/settings", json={"min_score": 0, "min_confidence": 0}).raise_for_status()
        with database() as conn:
            conn.execute("""
                UPDATE discovered_jobs SET discovered_at=discovered_at+interval '1 day'
                WHERE external_id='two'
            """)
        assert [r["title"] for r in client.get(base, params={"sort": "newest"}).json()["jobs"]] == [
            "Designer", "Engineer"]
        assert [r["title"] for r in client.get(base, params={"sort": "score"}).json()["jobs"]] == [
            "Engineer", "Designer"]
        store_listings(source, [post.model_copy(update={"external_id": f"page-{i}"}) for i in range(50)])
        page = client.get(base).json()
        continuation = client.get(base, params={"offset": 50}).json()
        assert page["has_more"] and len(page["jobs"]) == 50
        assert not continuation["has_more"] and len(continuation["jobs"]) == 2
        assert len({r["id"] for r in page["jobs"] + continuation["jobs"]}) == 52
