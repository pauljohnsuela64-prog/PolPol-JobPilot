"""One database-backed schedule shared by all local API workers."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from local_access import private_request
from database import database
from discovery_routes import fetch_enabled_boards
from shortlist import after_refresh

router = APIRouter(prefix="/api/discovery", dependencies=[Depends(private_request)])
fields = "enabled, interval_minutes, next_run_at, last_run_at, last_error"
log = logging.getLogger(__name__)


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=15, le=10080)


@router.get("/schedule")
def get_schedule():
    with database() as conn:
        return conn.execute(f"SELECT {fields} FROM discovery_schedule WHERE id = 1").fetchone()


@router.patch("/schedule")
def update_schedule(update: ScheduleUpdate):
    with database() as conn:
        return conn.execute(f"""
            UPDATE discovery_schedule SET
                next_run_at = CASE
                    WHEN NOT coalesce(%(enabled)s, enabled) THEN NULL
                    WHEN NOT enabled THEN clock_timestamp()
                    WHEN %(minutes)s::integer IS NOT NULL AND %(minutes)s <> interval_minutes
                        THEN clock_timestamp() + make_interval(mins => %(minutes)s)
                    ELSE next_run_at END,
                enabled = coalesce(%(enabled)s, enabled),
                interval_minutes = coalesce(%(minutes)s, interval_minutes)
            WHERE id = 1 RETURNING {fields}
        """,
        {"enabled": update.enabled, "minutes": update.interval_minutes}).fetchone()


def due(conn):
    return conn.execute("""
        SELECT 1 FROM discovery_schedule WHERE id = 1 AND enabled
        AND next_run_at <= clock_timestamp()
    """).fetchone() is not None


def run_refresh(scheduled=False):
    with database() as conn:
        conn.autocommit = True
        tables = conn.execute("""
            SELECT to_regclass('discovery_schedule') IS NOT NULL AS schedule,
                   to_regclass('shortlist_activity') IS NOT NULL AS shortlist,
                   to_regclass('career_boards') IS NOT NULL AS boards
        """).fetchone()
        if scheduled and (not tables["schedule"] or not due(conn)):
            return
        if not tables["boards"]:
            raise HTTPException(503, "Apply the discovery migration first.")
        # This session lock is released by PostgreSQL even if the process crashes.
        acquired = conn.execute("""
            SELECT pg_try_advisory_lock('career_boards'::regclass::oid::bigint) AS acquired
        """).fetchone()["acquired"]
        if not acquired:
            if scheduled:
                return
            raise HTTPException(409, "A refresh is already running. Wait for it to finish.")
        if scheduled and not due(conn):
            return
        error = "Discovery refresh failed."
        try:
            reports = fetch_enabled_boards(conn, tables["schedule"])
            error = "; ".join(f"{r['company']}: {r['error']}" for r in reports if r["error"])
            if tables["shortlist"]:
                with conn.transaction():
                    after_refresh(conn, reports)
        except Exception:
            error = "Discovery refresh failed."
            if tables["shortlist"]:
                with conn.transaction():
                    after_refresh(conn, [{"source_id": None, "company": "Discovery", "error": error,
                                          "seen": 0, "skipped": 0}])
            raise
        finally:
            if tables["schedule"]:
                conn.execute("""
                    UPDATE discovery_schedule SET last_run_at = clock_timestamp(), last_error = %s,
                    next_run_at = CASE WHEN enabled THEN clock_timestamp() +
                        make_interval(mins => interval_minutes) END WHERE id = 1
                """, (error[:1000] or None,))
        return reports


async def poll(stop):
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_refresh, scheduled=True)
        except Exception:
            log.warning("Could not complete the discovery schedule check.")
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(_app):
    stop = asyncio.Event()
    worker = asyncio.create_task(poll(stop))
    try:
        yield
    finally:
        stop.set()
        await worker

