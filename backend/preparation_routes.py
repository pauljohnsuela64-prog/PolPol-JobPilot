"""Local, versioned application preparation for discovered jobs."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from local_access import private_request
from builder import Master
from database import database
from preparation_content import export_content, generate_content


router = APIRouter(prefix="/api/preparation", dependencies=[Depends(private_request)])
Id = Annotated[int, Path(ge=1, le=2147483647)]
Positive = Annotated[int, Field(ge=1, le=2147483647, strict=True)]
Plain = Annotated[str, StringConstraints(strip_whitespace=True, max_length=16000,
    pattern=r"^[^\x00-\x09\x0b-\x1f\x7f\uFFFE\uFFFF]*$")]
Prompt = Annotated[Plain, Field(min_length=1, max_length=1500)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateDraft(Input):
    resume_id: Positive | None = None
    questions: list[Prompt] = Field(default_factory=list, max_length=40)


class Version(Input):
    revision: Positive


class Answer(Input):
    question: Prompt
    answer: Plain


class Item(Input):
    text: Prompt
    done: bool = Field(strict=True)


class EditDraft(Version):
    cover_letter: Annotated[Plain, Field(min_length=1)]
    answers: list[Answer] = Field(max_length=40)
    checklist: list[Item] = Field(max_length=60)


JOB_QUERY = "SELECT id, posting FROM discovered_jobs"


def snapshot(row):
    return {**row["posting"], "id": row["id"]}


def full(row):
    if not row:
        raise HTTPException(404, "Preparation draft not found.")
    return {**{k: v for k, v in row.items() if k != "content"}, **row["content"]}


def check_revision(db, draft_id, revision):
    """Lock this record so two windows cannot approve or overwrite stale edits."""
    row = db.execute("SELECT * FROM preparation_drafts WHERE id=%s FOR UPDATE", (draft_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Preparation draft not found.")
    if row["revision"] != revision:
        raise HTTPException(409, "Reload the current revision before continuing.")
    return row


@router.get("/jobs")
def jobs():
    with database() as db:
        return [snapshot(row) for row in db.execute(JOB_QUERY + " ORDER BY id DESC").fetchall()]


@router.get("/jobs/{job_id}/resumes")
def resumes(job_id: Id):
    with database() as db:
        return db.execute("""
            SELECT id,revision,created_at FROM resume_drafts
            WHERE listing_id=%s ORDER BY id DESC
        """, (job_id,)).fetchall()


@router.get("/jobs/{job_id}/drafts")
def history(job_id: Id):
    with database() as db:
        return db.execute("""
            SELECT id,revision,approved_revision,created_at FROM preparation_drafts
            WHERE job_id=%s ORDER BY id DESC
        """, (job_id,)).fetchall()


@router.post("/jobs/{job_id}/drafts", status_code=201)
def create(job_id: Id, body: CreateDraft):
    with database() as db:
        job = db.execute(JOB_QUERY + " WHERE id=%s", (job_id,)).fetchone()
        if not job:
            raise HTTPException(404, "Opening not found.")
        resume = None
        if body.resume_id is not None:
            resume = db.execute("""
                SELECT id,revision,content FROM resume_drafts WHERE id=%s AND listing_id=%s
            """, (body.resume_id, job_id)).fetchone()
            if not resume:
                raise HTTPException(422, "Choose a resume draft for this job.")
        facts = db.execute("SELECT facts FROM master_resume WHERE id=1").fetchone()
        posting = snapshot(job)
        content = generate_content(Master(**facts["facts"]) if facts else None, posting, body.questions)
        return full(db.execute("""
            INSERT INTO preparation_drafts(job_id,job_snapshot,resume_snapshot,content)
            VALUES(%s,%s,%s,%s) RETURNING *
        """, (job_id, Jsonb(posting), Jsonb(resume) if resume else None, Jsonb(content))).fetchone())


@router.get("/drafts/{draft_id}")
def read(draft_id: Id):
    with database() as db:
        return full(db.execute("SELECT * FROM preparation_drafts WHERE id=%s", (draft_id,)).fetchone())


@router.put("/drafts/{draft_id}")
def edit(draft_id: Id, body: EditDraft):
    with database() as db:
        check_revision(db, draft_id, body.revision)
        return full(db.execute("""
            UPDATE preparation_drafts SET content=%s, revision=revision+1, approved_revision=NULL
            WHERE id=%s RETURNING *
        """, (Jsonb(body.model_dump(exclude={"revision"})), draft_id)).fetchone())


@router.post("/drafts/{draft_id}/approve")
def approve(draft_id: Id, body: Version):
    with database() as db:
        check_revision(db, draft_id, body.revision)
        return full(db.execute("""
            UPDATE preparation_drafts SET approved_revision=revision WHERE id=%s RETURNING *
        """, (draft_id,)).fetchone())


@router.delete("/drafts/{draft_id}", status_code=204)
def delete(draft_id: Id):
    with database() as db:
        db.execute("DELETE FROM preparation_drafts WHERE id=%s", (draft_id,))
    return Response(status_code=204)


@router.post("/drafts/{draft_id}/export/{artifact}/{format}")
def export(draft_id: Id, artifact: Literal["cover_letter", "answers", "checklist"],
           format: Literal["pdf", "docx", "txt"], body: Version):
    with database() as db:
        row = check_revision(db, draft_id, body.revision)
        if row["approved_revision"] != body.revision:
            raise HTTPException(409, "Review and approve this revision before exporting.")
        data = export_content(row["content"], artifact, format)
    mime = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "txt": "text/plain"}
    return Response(data, media_type=mime[format], headers={
        "Content-Disposition": f'attachment; filename="preparation-{draft_id}-r{body.revision}-{artifact}.{format}"',
        "Cache-Control": "no-store",
    })
