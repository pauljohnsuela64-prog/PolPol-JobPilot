from pathlib import Path

import psycopg
import pytest
import httpx
from fastapi import HTTPException
from pydantic import ValidationError

import scheduler
import discovery_routes
from job_providers import fetch_board
from test_discovery import migration_database


def test_schedule_minimum_interval():
    with pytest.raises(ValidationError):
        scheduler.ScheduleUpdate(interval_minutes=14)


def test_restart_overlap_and_pause(migration_database, monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    with psycopg.connect(migration_database, autocommit=True) as admin:
        for file in [backend / "schema.sql", *sorted((backend / "migrations").glob("*.sql"))]:
            admin.execute(file.read_text())
        calls = []

        def mocked_boards(conn, persistent_cooldowns):
            calls.append("fetch")
            with pytest.raises(HTTPException) as busy:
                scheduler.run_refresh()
            assert busy.value.status_code == 409
            assert scheduler.run_refresh(scheduled=True) is None
            return [{"company": "Example", "source_id": None, "error": "Feed unavailable",
                     "seen": 0, "skipped": 0}]

        monkeypatch.setattr(scheduler, "fetch_enabled_boards", mocked_boards)
        monkeypatch.setattr(scheduler, "after_refresh", lambda conn, reports: calls.append("match"))
        assert scheduler.get_schedule()["enabled"] is False
        scheduler.run_refresh(scheduled=True)
        assert calls == []
        scheduler.update_schedule(scheduler.ScheduleUpdate(enabled=True, interval_minutes=30))

        # A different process held the claim and then disconnected before completion.
        with psycopg.connect(migration_database) as other:
            other.execute("SELECT pg_advisory_lock('career_boards'::regclass::oid::bigint)")
            scheduler.run_refresh(scheduled=True)
            assert calls == []
        scheduler.run_refresh(scheduled=True)
        scheduler.run_refresh(scheduled=True)
        assert calls == ["fetch", "match"]
        state = scheduler.get_schedule()
        assert state["next_run_at"] > state["last_run_at"]
        assert "Feed unavailable" in state["last_error"]

        paused = scheduler.update_schedule(scheduler.ScheduleUpdate(enabled=False))
        assert paused["next_run_at"] is None
        admin.execute((backend / "migrations/008_discovery_schedule.sql").read_text())
        assert scheduler.get_schedule() == paused
        scheduler.run_refresh(scheduled=True)
        assert calls == ["fetch", "match"]
        assert scheduler.run_refresh()[0]["company"] == "Example"
        assert scheduler.get_schedule()["next_run_at"] is None


def test_rate_limit_survives_restart_and_other_feed_still_matches(migration_database, monkeypatch):
    backend = Path(__file__).resolve().parents[1]
    requests = []

    def mocked_fetch(_, provider, board, company):
        def respond(request):
            requests.append(provider)
            if provider == "lever":
                return httpx.Response(429, headers={"Retry-After": "7200"})
            return httpx.Response(200, json={"jobs": [
                {"id": 12, "title": "Engineer", "absolute_url": "https://example.com/jobs/12"}
            ]})
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            return fetch_board(client, provider, board, company)

    monkeypatch.setattr(discovery_routes, "fetch_board", mocked_fetch)
    monkeypatch.setattr("job_providers.rate_until", {})
    with psycopg.connect(migration_database, autocommit=True) as db:
        for file in [backend / "schema.sql", *sorted((backend / "migrations").glob("*.sql"))]:
            db.execute(file.read_text())
        db.execute("""INSERT INTO career_boards(provider,board,company) VALUES
            ('lever','limited','Limited'), ('greenhouse','healthy','Healthy')""")
        db.execute("""UPDATE profile SET preferences='{"job_titles":["Engineer"]}' WHERE id=1""")
        scheduler.update_schedule(scheduler.ScheduleUpdate(enabled=True))
        scheduler.run_refresh(scheduled=True)
        # A fresh process has no in-memory cooldown. The database still holds the provider deadline.
        monkeypatch.setattr("job_providers.rate_until", {})
        scheduler.run_refresh()
        assert requests == ["lever", "greenhouse", "greenhouse"]
        assert db.execute("SELECT kind,count(*) FROM shortlist_activity GROUP BY kind ORDER BY kind").fetchall() == [
            ("new_match", 1), ("refresh_failed", 2)]
        assert db.execute("SELECT count(*) FROM discovered_jobs").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM applications").fetchone()[0] == 0
