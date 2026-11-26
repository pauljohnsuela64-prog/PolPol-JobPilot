"""Build a live shortlist from discovery's saved postings and profile evidence."""
import heapq
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from local_access import private_request
from database import database
from job_matching import Match, compare, compatibility
from job_providers import Arrangement, Posting, Provider, safe_posting_url
from profile_routes import Preferences


router = APIRouter(prefix="/api/shortlist", dependencies=[Depends(private_request)])
SETTINGS = "SELECT min_score, min_confidence FROM shortlist_settings WHERE id = 1"
Sort = Literal["best", "score", "confidence", "newest", "recent"]


class Thresholds(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    min_score: int = Field(ge=0, le=100)
    min_confidence: int = Field(ge=0, le=100)


class Review(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    reviewed: bool


@router.get("/settings")
def settings():
    with database() as connection:
        return connection.execute(SETTINGS).fetchone()


@router.put("/settings")
def update_settings(body: Thresholds):
    with database() as connection:
        return connection.execute("""
            UPDATE shortlist_settings SET min_score=%s, min_confidence=%s WHERE id=1
            RETURNING min_score, min_confidence
        """, (body.min_score, body.min_confidence)).fetchone()


def qualifying(connection, *, q="", reviewed=None, provider=None, source_id=None,
               arrangement=None, match=None, unseen_only=False):
    settings = connection.execute(SETTINGS).fetchone()
    prefs = Preferences.model_validate(connection.execute(
        "SELECT preferences FROM profile WHERE id=1").fetchone()["preferences"])
    with connection.cursor(name="shortlist_candidates") as cursor:
        cursor.execute("""
            SELECT d.id, d.source_id, b.board, d.provider, d.posting, d.reviewed,
                   d.discovered_at >= now() - interval '24 hours' AND NOT d.reviewed AS newly_discovered,
                   d.discovered_at >= now() - interval '24 hours' AND NOT d.reviewed AS newly_discovered,
                   d.discovered_at, d.last_seen_at, d.tracked_job_id
            FROM discovered_jobs d JOIN career_boards b ON b.id=d.source_id
            WHERE (%s::boolean IS NULL OR d.reviewed=%s)
              AND (%s::text IS NULL OR d.provider=%s)
              AND (%s::integer IS NULL OR d.source_id=%s)
              AND (%s::text IS NULL OR d.posting->>'arrangement'=%s)
              AND strpos(lower(concat_ws(' ', d.posting->>'title', d.posting->>'company',
                            d.posting->>'location')), lower(%s))>0
              AND (NOT %s OR NOT EXISTS (SELECT 1 FROM shortlist_activity a WHERE a.listing_id=d.id))
        """, (reviewed, reviewed, provider, provider, source_id, source_id, arrangement, arrangement,
              q.strip().replace("\x00", ""), unseen_only))
        for row in cursor:
            post = Posting.model_validate(row.pop("posting"))
            status, checks = compare(post, prefs)
            if status == "unconfigured" or (match and status != match):
                continue
            report = compatibility(post, prefs, checks)
            if report["score"] < settings["min_score"] or report["confidence"] < settings["min_confidence"]:
                continue
            hosts = {"jobs.lever.co", "jobs.eu.lever.co"} if row["provider"].startswith("lever") else None
            yield {**row, **post.model_dump(exclude={"description", "external_id"}),
                   "url": safe_posting_url(post.url, hosts) or None, "compatibility": report,
                   "match": status, "checks": checks}


@router.get("")
def jobs(q: Annotated[str, Query(max_length=120)] = "", reviewed: bool | None = None,
         provider: Provider | None = None, source_id: Annotated[int | None, Query(ge=1, le=2147483647)] = None,
         arrangement: Arrangement | None = None, match: Match | None = None, sort: Sort = "best",
         offset: Annotated[int, Query(ge=0, le=2147483647)] = 0):
    def rank(row):
        score = row["compatibility"]["score"]
        confidence = row["compatibility"]["confidence"]
        primary = {"best": score * confidence, "score": score, "confidence": confidence,
                   "newest": row["discovered_at"], "recent": row["last_seen_at"]}[sort]
        return primary, score, confidence, row["id"]

    with database() as connection:
        rows = heapq.nlargest(offset + 51, qualifying(connection, reviewed=reviewed, q=q, provider=provider,
                            source_id=source_id, arrangement=arrangement, match=match), key=rank)
        return {"jobs": rows[offset:offset + 50], "has_more": len(rows) > offset + 50}


@router.patch("/jobs/{listing_id}")
def set_reviewed(listing_id: Annotated[int, Path(ge=1, le=2147483647)], body: Review):
    with database() as connection:
        row = connection.execute("UPDATE discovered_jobs SET reviewed=%s WHERE id=%s RETURNING id, reviewed",
                                 (body.reviewed, listing_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Discovered job not found.")
        return row


def after_refresh(connection, reports):
    for report in reports:
        if report.get("error"):
            connection.execute("""
                INSERT INTO shortlist_activity (kind, source_id, company, detail)
                VALUES ('refresh_failed', %s, %s, %s)
            """, (report["source_id"], report["company"], report["error"]))
    for row in qualifying(connection, unseen_only=True):
        connection.execute("""
            INSERT INTO shortlist_activity (kind, listing_id, source_id, title, company, detail)
            VALUES ('new_match', %s, %s, %s, %s, %s) ON CONFLICT (listing_id) DO NOTHING
        """, (row["id"], row["source_id"], row["title"], row["company"],
              f"Compatibility {row['compatibility']['score']}%, confidence {row['compatibility']['confidence']}%."))

@router.get("/activity")
def activity():
    with database() as connection:
        return connection.execute("SELECT * FROM shortlist_activity ORDER BY id DESC LIMIT 100").fetchall()


