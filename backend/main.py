from datetime import datetime
from typing import Annotated, Literal

import psycopg
from fastapi import FastAPI, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

from database import database, track_job
from discovery_routes import router as discovery_router
from profile_routes import router as profile_router

app = FastAPI(title="PolPol JobPilot", docs_url=None, redoc_url=None)
app.include_router(profile_router)
app.include_router(discovery_router)

RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
OptionalText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
Stage = Literal["saved", "applied", "interview", "offer", "closed"]
JobId = Annotated[int, Path(ge=1, le=2_147_483_647)]


class NewJob(BaseModel):
    title: RequiredText
    company: RequiredText
    location: OptionalText = ""


class ApplicationUpdate(BaseModel):
    status: Stage


class TrackedJob(NewJob, ApplicationUpdate):
    job_id: int
    created_at: datetime


@app.exception_handler(psycopg.Error)
async def database_error(_request: Request, _error: psycopg.Error):
    return JSONResponse(status_code=503, content={"detail": "Check the local PostgreSQL connection and schema."})


@app.get("/health")
def health():
    """Report API availability independently of the database."""
    return {"status": "ok"}


@app.get("/api/jobs", response_model=list[TrackedJob])
def list_jobs():
    with database() as connection:
        return connection.execute(
            """
            SELECT j.id AS job_id, j.title, j.company, j.location, j.created_at, a.status
            FROM jobs AS j JOIN applications AS a ON a.job_id = j.id
            ORDER BY j.created_at DESC, j.id DESC
            """
        ).fetchall()


@app.post("/api/jobs", response_model=TrackedJob, status_code=201)
def create_job(job: NewJob):
    with database() as connection:
        return track_job(connection, job.title, job.company, job.location)

@app.patch("/api/jobs/{job_id}", response_model=TrackedJob)
def change_stage(job_id: JobId, application: ApplicationUpdate):
    # Updating and returning the joined record in one statement avoids a second read.
    with database() as connection:
        row = connection.execute(
            """
            UPDATE applications AS a SET status = %s
            FROM jobs AS j WHERE j.id = a.job_id AND a.job_id = %s
            RETURNING a.job_id, a.status, j.title, j.company, j.location, j.created_at
            """,
            (application.status, job_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Job not found.")
        return row
