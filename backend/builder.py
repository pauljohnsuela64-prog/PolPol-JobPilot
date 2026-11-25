"""Local resume generation, private draft history and reviewed exports."""
import io
import re
from pathlib import Path
from typing import Annotated, Literal

from xml.sax.saxutils import escape

from docx import Document
from docx.shared import Pt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate

from database import database
from job_matching import ALIASES, compare, compatibility, contains
from job_providers import Posting
from profile_routes import Preferences


def private_request(request: Request):
    origin = request.headers.get("origin")
    # Vite can rewrite Host to the API port. Browser-controlled fetch metadata
    # still identifies a same-origin request to the loopback frontend.
    proxied = (request.headers.get("sec-fetch-site") == "same-origin" and origin
               and re.fullmatch(r"http://(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?", origin))
    if (request.url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}
            or (origin and not proxied and origin != f"http://{request.headers.get('host')}")):
        raise HTTPException(403, "Use the local JobPilot window.")


router = APIRouter(prefix="/api/builder", dependencies=[Depends(private_request)])
Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500, pattern=r"^[^\x00-\x1f\x7f\uFFFE\uFFFF]+$")]
Entries = Annotated[list[Line], Field(max_length=40)]
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600000, pattern=r"^[^\x00-\x09\x0b-\x1f\x7f\uFFFE\uFFFF]+$")]


class Master(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[Line, Field(max_length=160)]
    contact: Entries = []
    summary: Line | None = None
    education: Entries = []
    experience: Entries = []
    skills: Entries = []
    projects: Entries = []
    achievements: Entries = []
    certifications: Entries = []


class Edit(BaseModel):
    content: Body
    revision: int = Field(ge=1)


class Approval(BaseModel):
    approved: Literal[True]
    revision: int = Field(ge=1)


class ApplicationApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool = Field(strict=True)
    revision: int = Field(ge=1, le=2147483647, strict=True)


SECTIONS = {"summary": "Professional Summary", "skills": "Technical Skills", "experience": "Experience",
            "projects": "Projects", "achievements": "Achievements", "education": "Education",
            "certifications": "Certifications"}


def tailor(master, posting, preferences, *, preserve_order=False):
    # Match against the manually confirmed master. Posting text can select facts but never supply prose.
    prefs = preferences.model_copy(update={"technical_skills": master.skills})
    evidence = compatibility(posting, prefs, compare(posting, prefs)[1])
    terms = {alias for item in evidence["skills"] if item["confirmed"]
             for alias in ALIASES.get(item["skill"], (item["skill"],))}
    def relevance(entry):
        return sum(contains(entry, term) for term in terms)
    title_terms = {word for word in re.findall(r"[a-z]{4,}", posting.title.lower())
                   if word not in {"junior", "senior", "entry", "level", "remote", "hybrid", "with"}}
    def entry_relevance(entry):
        return relevance(entry), sum(contains(entry, term) for term in title_terms)
    skills = master.skills if preserve_order else sorted(master.skills, key=relevance, reverse=True)
    relevant = [skill for skill in skills if relevance(skill)]
    summary = master.summary or ""
    if relevant:
        summary = (summary + " " + "Technical skills include " + ", ".join(relevant[:4]) + ".").strip()
    lines = [master.name, *master.contact]
    for key, heading in SECTIONS.items():
        if key == "summary":
            entries = [summary] if summary else []
        elif key == "skills":
            entries = [", ".join(skills)] if skills else []
        else:
            entries = getattr(master, key)
            if not preserve_order and key in {"experience", "projects", "achievements"}:
                entries = sorted(entries, key=entry_relevance, reverse=True)
            entries = ["- " + entry for entry in entries]
        if entries:
            lines.extend(["", heading, *entries])
    return "\n".join(lines), evidence


@router.get("/master")
def get_master():
    with database() as db:
        row = db.execute("SELECT facts FROM master_resume WHERE id=1").fetchone()
        return row["facts"] if row else None


@router.put("/master")
def save_master(master: Master):
    with database() as db:
        db.execute("INSERT INTO master_resume VALUES (1,%s) ON CONFLICT(id) DO UPDATE SET facts=excluded.facts",
                   (Jsonb(master.model_dump()),))
    return master


@router.get("/jobs")
def jobs():
    with database() as db:
        return db.execute("SELECT id, posting->>'title' AS title, posting->>'company' AS company FROM discovered_jobs ORDER BY id DESC").fetchall()


@router.post("/jobs/{listing_id}/drafts", status_code=201)
def generate(listing_id: int):
    with database() as db:
        row = db.execute("""
            SELECT d.posting, m.facts, p.preferences FROM discovered_jobs d
            CROSS JOIN profile p LEFT JOIN master_resume m ON m.id=1 WHERE d.id=%s AND p.id=1
        """, (listing_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Opening not found.")
        if not row["facts"]:
            raise HTTPException(409, "Save your verified master resume first.")
        content, evidence = tailor(Master(**row["facts"]), Posting(**row["posting"]), Preferences(**row["preferences"]))
        snapshot = {**row, "evidence": evidence}
        return db.execute("INSERT INTO resume_drafts(listing_id,source_snapshot,content) VALUES (%s,%s,%s) RETURNING id",
                          (listing_id, Jsonb(snapshot), content)).fetchone()


@router.get("/jobs/{listing_id}/drafts")
def history(listing_id: int):
    with database() as db:
        return db.execute("SELECT id,created_at,revision FROM resume_drafts WHERE listing_id=%s ORDER BY id DESC",
                          (listing_id,)).fetchall()


@router.get("/drafts/{draft_id}")
def read_draft(draft_id: int):
    with database() as db:
        # Reading the optional key keeps Phase 5 exports usable before migration 007.
        row = db.execute("""SELECT id,content,revision,(to_jsonb(resume_drafts)->>'approved_revision')::integer AS approved_revision,
                            source_snapshot->'evidence'->'skills' AS evidence,
                            source_snapshot->'posting'->>'description' AS description FROM resume_drafts WHERE id=%s""",
                         (draft_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Draft not found.")
        return row


@router.put("/drafts/{draft_id}")
def edit(draft_id: int, body: Edit):
    with database() as db:
        row = db.execute("UPDATE resume_drafts SET content=%s, revision=revision+1 WHERE id=%s AND revision=%s RETURNING revision",
                         (body.content, draft_id, body.revision)).fetchone()
        if not row:
            raise HTTPException(409, "Reload this draft before saving changes.")
        return row


@router.put("/drafts/{draft_id}/approval")
def set_application_approval(draft_id: int, body: ApplicationApproval):
    """Only the saved revision reviewed in this window can receive approval."""
    with database() as db:
        row = db.execute("""UPDATE resume_drafts SET approved_revision=%s
                            WHERE id=%s AND revision=%s RETURNING approved_revision""",
                         (body.revision if body.approved else None, draft_id, body.revision)).fetchone()
        if not row:
            raise HTTPException(409, "Reload the current draft before changing its approval.")
        return row


@router.delete("/drafts/{draft_id}", status_code=204)
def delete(draft_id: int):
    with database() as db:
        db.execute("DELETE FROM resume_drafts WHERE id=%s", (draft_id,))
    return Response(status_code=204)


def render(content: str, format: str) -> bytes:
    # Both renderers consume identical plain-text lines. Only standard section headings receive styling.
    out = io.BytesIO()
    lines = [line for line in content.splitlines() if line.strip()]
    if format == "docx":
        doc = Document()
        normal = doc.styles["Normal"]
        normal.font.name = "Arial"
        normal.font.size = Pt(11)
        for index, line in enumerate(lines):
            if index == 0 or line in SECTIONS.values():
                doc.add_heading(line, level=0 if index == 0 else 1)
            else:
                doc.add_paragraph(line)
        doc.save(out)
    else:
        styles = getSampleStyleSheet()
        font = Path("C:/Windows/Fonts/arial.ttf")
        if font.is_file():
            pdfmetrics.registerFont(TTFont("JobPilotArial", str(font)))
        story = []
        for index, line in enumerate(lines):
            style = styles["Title" if index == 0 else "Heading2" if line in SECTIONS.values() else "BodyText"]
            if font.is_file():
                style.fontName = "JobPilotArial"
            story.append(Paragraph(escape(line), style))
        SimpleDocTemplate(out, pagesize=A4).build(story)
    return out.getvalue()


@router.post("/drafts/{draft_id}/export/{format}")
def export(draft_id: int, format: Literal["pdf", "docx"], approval: Approval):
    draft = read_draft(draft_id)
    if approval.revision != draft["revision"]:
        raise HTTPException(409, "Reload and approve the current revision.")
    mime = "application/pdf" if format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(render(draft["content"], format), media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="resume-{draft_id}-r{approval.revision}.{format}"',
        "Cache-Control": "no-store"})

