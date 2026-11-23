import io
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import unquote
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from starlette.concurrency import run_in_threadpool

from database import database

router = APIRouter(prefix="/api/profile")
logger = logging.getLogger(__name__)
MAX_RESUME_BYTES = 5 * 1024 * 1024
PreferenceItem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_titles: list[PreferenceItem] = Field(default_factory=list, max_length=30)
    locations: list[PreferenceItem] = Field(default_factory=list, max_length=30)
    work_arrangement: Literal["any", "remote", "hybrid", "on-site"] = "any"
    minimum_salary_php: int | None = Field(default=None, ge=0, le=999_999_999, strict=True)
    experience_level: Literal["internship", "entry-level", "junior"] | None = None
    technical_skills: list[PreferenceItem] = Field(default_factory=list, max_length=30)


class Resume(BaseModel):
    filename: str
    uploaded_at: datetime


class Profile(BaseModel):
    preferences: Preferences
    resume: Resume | None


def resume_directory() -> Path:
    default = Path(os.getenv("LOCALAPPDATA") or Path.home() / ".local" / "share") / "PolPolJobPilot" / "resumes"
    directory = Path(os.getenv("JOBPILOT_RESUME_DIR") or default).expanduser().resolve()
    if directory.is_relative_to(Path(__file__).resolve().parents[1]):
        raise HTTPException(503, "Choose a resume storage folder outside the project.")
    return directory


def validate_pdf(data: bytearray) -> None:
    # Parsing the page tree catches renamed files and fake or truncated PDF headers.
    try:
        if not data.startswith(b"%PDF-"):
            raise ValueError
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted or not reader.pages:
            raise ValueError
    except (PyPdfError, ValueError, IndexError, KeyError, TypeError, RecursionError, OverflowError):
        raise HTTPException(422, "Choose a readable, unencrypted PDF with at least one page.") from None


def remove_file(directory: Path, key: UUID | None) -> None:
    if key is not None:
        try:
            (directory / f"{key}.pdf").unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove an unreferenced resume file (%s).", key)


@router.get("", response_model=Profile)
def get_profile():
    with database() as connection:
        row = connection.execute(
            "SELECT preferences, resume_filename, resume_uploaded_at FROM profile WHERE id = 1"
        ).fetchone()
    resume = None
    if row["resume_filename"] is not None:
        resume = Resume(filename=row["resume_filename"], uploaded_at=row["resume_uploaded_at"])
    return Profile(preferences=row["preferences"], resume=resume)


@router.put("/preferences", response_model=Preferences)
def save_preferences(preferences: Preferences):
    with database() as connection:
        connection.execute(
            "UPDATE profile SET preferences = %s WHERE id = 1",
            (Jsonb(preferences.model_dump()),),
        )
    return preferences


def store_resume(filename: str, data: bytearray) -> Resume:
    validate_pdf(data)
    directory = resume_directory()
    key = uuid4()
    # A row lock serializes replacements and deletes. Keep the old file until the new
    # metadata commits, so a failed write cannot replace the current resume.
    with database() as connection:
        previous = connection.execute("SELECT resume_key FROM profile WHERE id = 1 FOR UPDATE").fetchone()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / f"{key}.pdf").open("xb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
        except OSError:
            remove_file(directory, key)
            raise HTTPException(503, "Could not save the resume to local storage.") from None
        resume = connection.execute(
            """
            UPDATE profile SET resume_key = %s, resume_filename = %s, resume_uploaded_at = now()
            WHERE id = 1 RETURNING resume_filename AS filename, resume_uploaded_at AS uploaded_at
            """,
            (key, filename),
        ).fetchone()
    remove_file(directory, previous["resume_key"])
    return Resume(**resume)


@router.put("/resume", response_model=Resume)
async def upload_resume(request: Request, x_resume_filename: Annotated[str, Header(max_length=1500)]):
    if request.headers.get("content-type") != "application/pdf":
        raise HTTPException(415, "Upload a PDF file.")
    filename = unquote(x_resume_filename).strip()
    if not filename or len(filename) > 255 or not filename.isprintable() or "/" in filename or "\\" in filename:
        raise HTTPException(422, "Choose a file with a valid filename of up to 255 characters.")

    # Count actual bytes as they arrive, even if Content-Length is absent.
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_RESUME_BYTES:
            raise HTTPException(413, "The resume must be 5 MB or smaller.")
        data.extend(chunk)
    return await run_in_threadpool(store_resume, filename, data)


@router.delete("/resume", status_code=204)
def delete_resume():
    directory = resume_directory()
    with database() as connection:
        previous = connection.execute("SELECT resume_key FROM profile WHERE id = 1 FOR UPDATE").fetchone()
        connection.execute(
            "UPDATE profile SET resume_key = NULL, resume_filename = NULL, resume_uploaded_at = NULL WHERE id = 1"
        )
    remove_file(directory, previous["resume_key"])
    return Response(status_code=204)
