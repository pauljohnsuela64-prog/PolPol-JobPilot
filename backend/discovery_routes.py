import re
import threading
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, StringConstraints

from database import database, track_job
from job_matching import Match, compare
from job_providers import BOARD_PATTERN, FeedError, Posting, Provider, fetch_board
from profile_routes import Preferences

def check_browser_origin(request: Request):
    origin = request.headers.get("origin")
    if origin is not None and not re.fullmatch(r"http://(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?", origin):
        raise HTTPException(403, "Open JobPilot from your local browser address.")


router = APIRouter(prefix="/api/discovery", dependencies=[Depends(check_browser_origin)])
refresh_lock = threading.Lock()
RecordId = Annotated[int, Path(ge=1, le=2_147_483_647)]


class NewSource(BaseModel):
    provider: Provider
    board: Annotated[str, StringConstraints(strip_whitespace=True, pattern=BOARD_PATTERN)]
    company: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120,
                                             pattern=r"^[^\x00-\x1f\x7f]+$")]


class SourceState(BaseModel):
    enabled: bool


@router.get("/sources")
def list_sources():
    with database() as connection:
        return connection.execute("SELECT * FROM career_boards ORDER BY company, id").fetchall()


@router.post("/sources", status_code=201)
def add_source(source: NewSource):
    with database() as connection:
        return connection.execute(
            """
            INSERT INTO career_boards (provider, board, company) VALUES (%s, %s, %s)
            ON CONFLICT (provider, board) DO UPDATE SET company = EXCLUDED.company
            RETURNING *
            """,
            (source.provider, source.board, source.company),
        ).fetchone()


@router.patch("/sources/{source_id}")
def set_source_state(source_id: RecordId, state: SourceState):
    with database() as connection:
        row = connection.execute(
            "UPDATE career_boards SET enabled = %s WHERE id = %s RETURNING *",
            (state.enabled, source_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Career board not found.")
        return row


def store_listings(source, jobs: list[Posting]) -> None:
    # The unique key keeps one listing per provider posting and preserves its first
    # discovery date and dashboard link when the public job is seen again.
    with database() as connection, connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO discovered_jobs (source_id, provider, external_id, posting)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (provider, external_id) DO UPDATE
            SET posting = EXCLUDED.posting, last_seen_at = clock_timestamp()
            """,
            [(source["id"], source["provider"], job.external_id, Jsonb(job.model_dump(mode="json")))
             for job in sorted(jobs, key=lambda job: job.external_id)],
        )


@router.post("/refresh")
def refresh():
    # The local server uses one worker. Ignore overlapping clicks across tabs.
    if not refresh_lock.acquire(blocking=False):
        raise HTTPException(409, "A refresh is already running. Wait for it to finish.")
    try:
        with database() as connection:
            sources = connection.execute("SELECT * FROM career_boards WHERE enabled ORDER BY id").fetchall()
        reports = []
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            for source in sources:
                try:
                    jobs, rejected = fetch_board(client, source["provider"], source["board"], source["company"])
                except FeedError as error:
                    reports.append({"source_id": source["id"], "company": source["company"], "board": source["board"],
                                    "seen": 0, "skipped": 0, "error": str(error)})
                    continue
                store_listings(source, jobs)
                reports.append({"source_id": source["id"], "company": source["company"], "board": source["board"],
                                "seen": len(jobs), "skipped": rejected, "error": None})
        return reports
    finally:
        refresh_lock.release()


@router.get("/jobs")
def list_listings(
    q: Annotated[str, Query(max_length=120)] = "",
    provider: Provider | None = None,
    match: Match | None = None,
    offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
):
    query = q.strip().replace("\x00", "")
    with database() as connection:
        settings = connection.execute("SELECT preferences FROM profile WHERE id = 1").fetchone()
        preferences = Preferences.model_validate(settings["preferences"])
        with connection.cursor(name="discovery_page") as cursor:
            cursor.execute(
                """
                SELECT id, provider, posting, discovered_at, last_seen_at, tracked_job_id
                FROM discovered_jobs
                WHERE (%s::text IS NULL OR provider = %s)
                  AND strpos(lower(concat_ws(' ', posting->>'title', posting->>'company', posting->>'location')),
                             lower(%s)) > 0
                ORDER BY discovered_at DESC, id DESC
                """,
                (provider, provider, query),
            )
            results = []
            count = 0
            has_more = False
            for row in cursor:
                post = Posting.model_validate(row.pop("posting"))
                status, checks = compare(post, preferences)
                if match is not None and status != match:
                    continue
                count += 1
                if count <= offset:
                    continue
                if len(results) == 50:
                    has_more = True
                    break
                results.append({**row, **post.model_dump(exclude={"description", "external_id"}),
                                "match": status, "checks": checks})
    return {"jobs": results, "has_more": has_more}


@router.post("/jobs/{listing_id}/save")
def save_listing(listing_id: RecordId):
    # Lock the listing so simultaneous saves cannot create two tracked jobs.
    with database() as connection:
        row = connection.execute(
            "SELECT posting, tracked_job_id FROM discovered_jobs WHERE id = %s FOR UPDATE",
            (listing_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Discovered job not found.")
        if row["tracked_job_id"] is not None:
            return {"job_id": row["tracked_job_id"]}
        post = Posting.model_validate(row["posting"])
        tracked = track_job(connection, post.title, post.company, (post.location or "")[:120])
        connection.execute(
            "UPDATE discovered_jobs SET tracked_job_id = %s WHERE id = %s",
            (tracked["job_id"], listing_id),
        )
        return {"job_id": tracked["job_id"]}
