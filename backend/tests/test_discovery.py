import os
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

import discovery_routes
from job_matching import compare
from job_providers import FeedError, Posting, Salary, fetch_board, rate_until
from main import app
from profile_routes import Preferences


GREENHOUSE = {
    "id": 123,
    "title": "Junior Developer",
    "location": {"name": "Manila, Philippines"},
    "absolute_url": "https://example.com/careers/jobs/123",
    "first_published": "2026-01-01T00:00:00Z",
    "metadata": [{"name": "Workplace Type", "value": "Hybrid"}],
    "content": "&lt;p&gt;Experience with Python and PostgreSQL.&lt;/p&gt;",
}
LEVER = {
    "id": "posting-abc",
    "text": "QA Engineer",
    "categories": {"location": "Manila", "allLocations": ["Manila", "Cebu"]},
    "hostedUrl": "https://jobs.lever.co/example/posting-abc",
    "workplaceType": "remote",
    "salaryRange": {"currency": "PHP", "interval": "per-month", "min": 40000, "max": 50000},
    "lists": [{"text": "Skills", "content": "<li>Python</li>"}],
}


@pytest.fixture(autouse=True)
def reset_cooldowns():
    rate_until.clear()
    yield
    rate_until.clear()


def board_request(handler, provider="greenhouse"):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return fetch_board(client, provider, "example", "Example Co")


def test_greenhouse_normalizes_post_and_skips_unsafe_url():
    def handler(request):
        assert request.method == "GET"
        assert request.url.host == "boards-api.greenhouse.io"
        assert request.url.path == "/v1/boards/example/jobs"
        assert dict(request.url.params) == {"content": "true"}
        return httpx.Response(200, json={"jobs": [
            GREENHOUSE, dict(GREENHOUSE, id=124, absolute_url="javascript:alert(1)"),
            dict(GREENHOUSE, id=125, absolute_url="https://trusted.example@evil.example/job"),
        ]})

    jobs, skipped = board_request(handler)
    assert skipped == 2
    assert len(jobs) == 1
    assert jobs[0].company == "Example Co"
    assert jobs[0].description == "Experience with Python and PostgreSQL."
    assert jobs[0].arrangement == "hybrid"
    assert jobs[0].salary is None
    assert jobs[0].posted_at.year == 2026


def test_lever_uses_pagination_and_normalizes_structured_salary():
    pages = []

    def handler(request):
        assert request.url.host == "api.eu.lever.co"
        skip = int(request.url.params["skip"])
        pages.append(skip)
        return httpx.Response(200, json=[LEVER] * 100 if skip == 0 else [])

    jobs, skipped = board_request(handler, "lever-eu")
    assert pages == [0, 100]
    assert (len(jobs), skipped) == (1, 0)
    assert jobs[0].location == "Manila, Cebu"
    assert jobs[0].arrangement == "remote"
    assert jobs[0].salary.minimum == 40000
    assert jobs[0].description == "Python"
    assert jobs[0].posted_at is None


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"data": []}),
    httpx.Response(200, headers={"content-type": "application/json"}, text="not JSON"),
    httpx.Response(302, headers={"location": "http://127.0.0.1/private"}),
    httpx.Response(404),
], ids=["wrong-shape", "invalid-json", "redirect", "not-found"])
def test_rejects_invalid_feed_without_following_redirect(response):
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return response

    with pytest.raises(FeedError):
        board_request(handler)
    assert hosts == ["boards-api.greenhouse.io"]


def test_transient_failure_retries_only_once():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private exception details")

    with pytest.raises(FeedError, match="Could not reach the provider"):
        board_request(handler)
    assert len(calls) == 2


def test_rate_limit_blocks_subsequent_refresh_without_another_request():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"})

    for _ in range(2):
        with pytest.raises(FeedError, match="rate limit"):
            board_request(handler)
    assert len(calls) == 1


@pytest.mark.parametrize(("provider", "board"), [
    ("greenhouse", "https://localhost/private"),
    ("unknown", "example"),
    ("lever", "../example"),
])
def test_discovery_source_validates_provider_and_board(provider, board):
    with TestClient(app) as client:
        response = client.post("/api/discovery/sources", json={
            "provider": provider, "board": board, "company": "Example",
        })
    assert response.status_code == 422


def test_discovery_blocks_foreign_browser_origin():
    with TestClient(app) as client:
        assert client.post("/api/discovery/refresh", headers={"Origin": "https://unrelated.example"}).status_code == 403


def test_all_preference_fields_have_evidence():
    post = Posting(
        external_id="one", title="Junior Developer", company="Example", location="Manila, Philippines",
        arrangement="hybrid", salary=Salary(currency="PHP", interval="per-month", minimum=35000, maximum=45000),
        url="https://example.com/job", description="Python and PostgreSQL",
    )
    prefs = Preferences(job_titles=["QA Engineer", "Developer"], locations=["Manila"],
                        technical_skills=["Python", "PostgreSQL"], work_arrangement="hybrid",
                        minimum_salary_php=35000, experience_level="junior")
    assert compare(post, prefs) == ("match", dict.fromkeys(
        ["Title", "Location", "Skills", "Work arrangement", "Experience", "Salary"], "match"))
    status, checks = compare(post, prefs.model_copy(update={"work_arrangement": "on-site"}))
    assert status == checks["Work arrangement"] == "not_match"
    status, checks = compare(post, prefs.model_copy(update={"minimum_salary_php": 50000}))
    assert status == checks["Salary"] == "not_match"


def test_missing_and_uncertain_evidence_stays_visible():
    post = Posting(external_id="one", title="Engineer", company="Example", url="https://example.com/job")
    prefs = Preferences(work_arrangement="remote", minimum_salary_php=35000, experience_level="entry-level")
    assert compare(post, prefs) == ("missing", {"Work arrangement": "missing", "Experience": "missing", "Salary": "missing"})
    for currency, interval, minimum in [("USD", "month", 40000), ("PHP", "year", 40000), ("PHP", "month", 30000)]:
        salary = Salary(currency=currency, interval=interval, minimum=minimum, maximum=50000)
        status, checks = compare(post.model_copy(update={"salary": salary}), prefs)
        assert status == checks["Salary"] == "uncertain"
        assert checks["Work arrangement"] == "missing"


@pytest.fixture
def migration_database(monkeypatch):
    url = os.getenv("JOBPILOT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set JOBPILOT_TEST_DATABASE_URL to a disposable PostgreSQL database")
    name = "discovery_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        try:
            isolated = make_conninfo(url, options=f"-c search_path={name}")
            monkeypatch.setenv("DATABASE_URL", isolated)
            yield isolated
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def test_refresh_save_and_migration_preserve_previous_data(migration_database, monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    with psycopg.connect(migration_database, autocommit=True) as connection, TestClient(app) as client:
        for file in ("schema.sql", "migrations/002_profile.sql"):
            connection.execute((backend / file).read_text())
        job = client.post("/api/jobs", json={"title": "Existing", "company": "Old Co"}).json()
        old_job = client.patch(f"/api/jobs/{job['job_id']}", json={"status": "interview"}).json()
        client.put("/api/profile/preferences", json={"job_titles": ["Developer"], "minimum_salary_php": 30000}).raise_for_status()
        connection.execute(
            "UPDATE profile SET resume_key = %s, resume_filename = %s, resume_uploaded_at = now() WHERE id = 1",
            (uuid4(), "Existing.pdf"),
        )
        old_profile = client.get("/api/profile").json()
        migration = (backend / "migrations/003_discovery.sql").read_text()
        connection.execute(migration)
        connection.execute(migration)

        feed = [GREENHOUSE, GREENHOUSE]
        attempted = []

        def network(request):
            attempted.append(request.url.path)
            assert request.content == b""
            if "unavailable" in request.url.path:
                return httpx.Response(404)
            return httpx.Response(200, json={"jobs": feed})

        def using_mock(_client, provider, board, company):
            with httpx.Client(transport=httpx.MockTransport(network)) as feed_client:
                return fetch_board(feed_client, provider, board, company)

        monkeypatch.setattr(discovery_routes, "fetch_board", using_mock)
        source = client.post("/api/discovery/sources", json={
            "provider": "greenhouse", "board": "example", "company": "Example Co",
        }).json()
        client.post("/api/discovery/sources", json={
            "provider": "greenhouse", "board": "unavailable", "company": "Unavailable",
        }).raise_for_status()
        assert client.post("/api/discovery/sources", json={
            "provider": "greenhouse", "board": "example", "company": "Example Co",
        }).json()["id"] == source["id"]

        reports = client.post("/api/discovery/refresh").json()
        assert reports[0]["seen"] == 1
        assert "Board not found" in reports[1]["error"]
        first, = client.get("/api/discovery/jobs?match=missing&q=developer").json()["jobs"]
        assert first["checks"] == {"Title": "match", "Salary": "missing"}
        assert client.get("/api/jobs").json() == [old_job]

        feed[:] = [dict(GREENHOUSE, location={"name": "Cebu"})]
        client.post("/api/discovery/refresh").raise_for_status()
        second, = client.get("/api/discovery/jobs?provider=greenhouse").json()["jobs"]
        assert second["id"] == first["id"]
        assert second["discovered_at"] == first["discovered_at"]
        assert second["last_seen_at"] > first["last_seen_at"]
        assert second["location"] == "Cebu"

        feed.clear()
        client.post("/api/discovery/refresh").raise_for_status()
        assert client.get("/api/discovery/jobs").json()["jobs"] == [second]
        client.patch(f"/api/discovery/sources/{source['id']}", json={"enabled": False}).raise_for_status()
        attempted.clear()
        client.post("/api/discovery/refresh").raise_for_status()
        assert attempted == ["/v1/boards/unavailable/jobs"]
        saved = client.post(f"/api/discovery/jobs/{first['id']}/save").json()
        assert client.post(f"/api/discovery/jobs/{first['id']}/save").json() == saved
        tracked, original = client.get("/api/jobs").json()
        assert original == old_job
        assert tracked["job_id"] == saved["job_id"] and tracked["status"] == "saved"
        assert client.get("/api/profile").json() == old_profile


@pytest.mark.skipif(os.getenv("JOBPILOT_LIVE_FEEDS") != "1", reason="Optional public network smoke test")
@pytest.mark.parametrize(("provider", "board", "company"), [
    ("greenhouse", "stripe", "Stripe"),
    ("lever", "spotify", "Spotify"),
])
def test_live_public_board(provider, board, company):
    with httpx.Client(trust_env=False) as client:
        jobs, _ = fetch_board(client, provider, board, company)
    assert jobs
    assert all(job.url.startswith("https://") for job in jobs)


def test_compatibility_weights_aliases_and_unverified_skills():
    from job_matching import compatibility

    post = Posting(external_id="one", company="Example", title="Junior Developer", location="Manila",
                   arrangement="remote", url="https://example.com/job",
                   description="Required: JS, Postgres. Preferred: Active Directory. Team uses JavaBeans.",
                   salary=Salary(currency="PHP", interval="month", minimum=35000, maximum=45000))
    prefs = Preferences(job_titles=["Developer"], locations=["Manila"], technical_skills=["JavaScript", "PostgreSQL"],
                        work_arrangement="remote", minimum_salary_php=30000, experience_level="junior")
    def assess(job, profile):
        return compatibility(job, profile, compare(job, profile)[1])

    report = assess(post, prefs)
    assert report["score"] == report["confidence"] == 90
    assert [(s["skill"], s["context"], s["confirmed"]) for s in report["skills"]] == [
        ("JavaScript", "required", True), ("PostgreSQL", "required", True), ("Active Directory", "preferred", False)]
    full = prefs.model_copy(update={"technical_skills": [*prefs.technical_skills, "Active Directory"]})
    assert assess(post, full)["score"] == 100
    other = post.model_copy(update={"title": "Senior Developer", "salary": None, "description": "Python is not required. 3 years of experience."})
    result = assess(other, prefs)
    assert result["criteria"]["Experience"] == "not_match"
    assert result["criteria"]["Salary"] == "missing"
    assert result["skills"][0]["context"] == "mentioned"
    assert result["experience_evidence"] == ["3 years of experience"]
    assert assess(other.model_copy(update={"salary": post.salary.model_copy(update={"currency": "USD"})}), prefs)["criteria"]["Salary"] == "uncertain"
    assert assess(other, Preferences(job_titles=["Nurse"]))["score"] == 0


def test_ranking_before_pagination_and_profile_changes(migration_database):
    backend = Path(__file__).resolve().parents[1]
    with psycopg.connect(migration_database, autocommit=True) as db, TestClient(app) as client:
        for file in ("schema.sql", "migrations/002_profile.sql", "migrations/003_discovery.sql"):
            db.execute((backend / file).read_text())
        source = client.post("/api/discovery/sources", json={"provider": "lever", "board": "sample", "company": "Fixture"}).json()
        postings = [Posting(external_id=f"{i:03}", title="Analyst" if i == 0 else "Artist",
                            company="Fixture", url="https://example.com/job") for i in range(52)]
        discovery_routes.store_listings(source, postings)
        client.put("/api/profile/preferences", json={"job_titles": ["Analyst"]}).raise_for_status()
        def jobs(query):
            return client.get("/api/discovery/jobs?" + query).json()["jobs"]
        first = jobs("sort=best")
        assert len(first) == 50 and first[0]["title"] == "Analyst"
        assert first == jobs("sort=best")
        assert jobs("sort=score")[0]["title"] == "Analyst"
        assert jobs("sort=newest&offset=50")[-1]["title"] == "Analyst"
        assert jobs("sort=recent")[0]["title"] == "Artist"
        client.put("/api/profile/preferences", json={"job_titles": ["Artist"]}).raise_for_status()
        assert jobs("sort=best")[0]["title"] == "Artist"
