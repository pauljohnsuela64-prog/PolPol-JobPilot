from itertools import islice

import heapq
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, StringConstraints

from local_access import private_request
from database import database, track_job
from job_matching import Match, compare, compatibility
from job_providers import BOARD_PATTERN, HOSTS, FeedError, FeedRateLimit, Posting, Provider, fetch_board
from profile_routes import Preferences

router = APIRouter(prefix="/api/discovery", dependencies=[Depends(private_request)])
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
    from scheduler import run_refresh
    return run_refresh()


def fetch_enabled_boards(connection, persistent_cooldowns):
    sources = connection.execute("SELECT * FROM career_boards WHERE enabled ORDER BY id").fetchall()
    reports = []
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        for source in sources:
            host = HOSTS[source["provider"]]
            try:
                if persistent_cooldowns and connection.execute(
                    "SELECT 1 FROM discovery_provider_cooldowns WHERE host=%s AND retry_at > clock_timestamp()",
                    (host,),
                ).fetchone():
                    raise FeedError("Provider rate limit reached. Try refreshing later.")
                jobs, rejected = fetch_board(client, source["provider"], source["board"], source["company"])
            except FeedError as error:
                if persistent_cooldowns and isinstance(error, FeedRateLimit):
                    connection.execute("""
                        INSERT INTO discovery_provider_cooldowns (host, retry_at)
                        VALUES (%s, clock_timestamp() + make_interval(secs => %s))
                        ON CONFLICT (host) DO UPDATE SET retry_at = GREATEST(
                            discovery_provider_cooldowns.retry_at, EXCLUDED.retry_at)
                    """, (host, error.seconds))
                reports.append({"source_id": source["id"], "company": source["company"], "board": source["board"],
                                "seen": 0, "skipped": 0, "error": str(error)})
                continue
            store_listings(source, jobs)
            reports.append({"source_id": source["id"], "company": source["company"], "board": source["board"],
                            "seen": len(jobs), "skipped": rejected, "error": None})
    return reports

@router.get("/jobs")
def list_listings(
    q: Annotated[str, Query(max_length=120)] = "",
    provider: Provider | None = None,
    match: Match | None = None,
    sort: Literal["best", "score", "newest", "recent"] = "best",
    offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
):
    def rank(row):
        report = row["compatibility"]
        if sort == "best":
            # Combine compatibility with evidence coverage before breaking ties by score.
            return (report["score"] * report["confidence"], report["score"], row["id"])
        return (report["score"], report["confidence"], row["id"])

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
                ORDER BY CASE WHEN %s = 'recent' THEN last_seen_at ELSE discovered_at END DESC, id DESC
                """,
                (provider, provider, query, sort),
            )
            def matching_rows():
                for row in cursor:
                    post = Posting.model_validate(row.pop("posting"))
                    status, checks = compare(post, preferences)
                    if match is not None and status != match:
                        continue
                    yield {**row, **post.model_dump(exclude={"description", "external_id"}),
                                    "compatibility": compatibility(post, preferences, checks), "match": status, "checks": checks}

            # Keep at most the requested prefix in memory. Date sorts already arrive in SQL order.
            if sort in ("recent", "newest"):
                results = list(islice(matching_rows(), offset + 51))
            else:
                results = heapq.nlargest(offset + 51, matching_rows(), key=rank)
    return {"jobs": results[offset:offset + 50], "has_more": len(results) > offset + 50}


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
