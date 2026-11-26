from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StringConstraints, field_validator

from local_access import private_request
from database import database, track_job
from job_providers import safe_posting_url

router = APIRouter(prefix="/api/queue", dependencies=[Depends(private_request)])
Key = Annotated[str, Path(pattern=r"^(job|listing)-[1-9][0-9]{0,9}$")]
Channel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: StrictBool
    channel: Channel
    reference: str = Field(default="", max_length=240)
    note: Note = ""
    follow_up_on: date | None = None
    submitted_at: AwareDatetime | None = None
    acknowledge_history: bool = Field(default=False, strict=True)


    @field_validator("channel", "reference", "note")
    @classmethod
    def reject_controls(cls, value, info):
        allowed = "\n\r\t" if info.field_name == "note" else ""
        if any(not c.isprintable() and c not in allowed for c in value):
            raise ValueError("Text contains a control character.")
        return value

    @field_validator("submitted_at")
    @classmethod
    def past_timestamp(cls, value):
        if value and value > datetime.now(timezone.utc):
            raise ValueError("Submission time must be in the past or present.")
        return value

    @field_validator("confirmed")
    @classmethod
    def require_confirmation(cls, value):
        if not value:
            raise ValueError("Explicit confirmation is required.")
        return value


class Correction(Submission):
    note: Note = Field(min_length=1)
    expected_event_id: int = Field(gt=0, le=2147483647)


QUEUE = """
    SELECT j.id AS job_id, d.id AS listing_id,
           coalesce(j.title,d.posting->>'title') AS title,
           coalesce(j.company,d.posting->>'company') AS company,
           coalesce(j.location,d.posting->>'location','') AS location,
           a.status, d.provider, d.posting->>'url' AS url,
           p.id AS prep_id, p.revision AS prep_revision,
           coalesce(p.revision=p.approved_revision,false) AS prep_approved,
           (p.resume_snapshot->>'id')::integer AS resume_id,
           (p.resume_snapshot->>'revision')::integer AS resume_revision,
           r.revision AS current_resume_revision, r.approved_revision AS approved_resume_revision,
           coalesce(r.revision=r.approved_revision AND
                    r.revision=(p.resume_snapshot->>'revision')::integer,false) AS resume_approved,
           coalesce(h.history,'[]'::jsonb) AS history,
           EXISTS (
               SELECT 1 FROM jobs other JOIN applications oa ON oa.job_id=other.id
               WHERE other.id IS DISTINCT FROM j.id
                 AND lower(trim(other.title))=lower(trim(coalesce(j.title,d.posting->>'title')))
                 AND lower(trim(other.company))=lower(trim(coalesce(j.company,d.posting->>'company')))
                 AND (oa.status <> 'saved' OR EXISTS (
                     SELECT 1 FROM application_queue_history oh WHERE oh.job_id=other.id))
           ) AS similar
    FROM jobs j FULL JOIN discovered_jobs d ON d.tracked_job_id=j.id
    LEFT JOIN applications a ON a.job_id=j.id
    LEFT JOIN LATERAL (
        SELECT * FROM preparation_drafts WHERE job_id=d.id ORDER BY id DESC LIMIT 1
    ) p ON true
    LEFT JOIN resume_drafts r ON r.listing_id=d.id AND r.id=(p.resume_snapshot->>'id')::integer
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(to_jsonb(e)-'job_id' ORDER BY e.id DESC) AS history
        FROM application_queue_history e WHERE e.job_id=j.id
    ) h ON true
"""


def queue_row(row):
    row = dict(row)
    latest = next(iter(row["history"]), None)
    row["key"] = f"listing-{row['listing_id']}" if row["listing_id"] else f"job-{row['job_id']}"
    hosts = {"jobs.lever.co", "jobs.eu.lever.co"} if row.get("provider") in ("lever", "lever-eu") else None
    row["url"] = safe_posting_url(row["url"], hosts) or None
    row["history_unknown"] = bool(row["status"] and row["status"] != "saved" and not latest)
    similar = row.pop("similar")
    row["duplicate_warning"] = ("Submission already logged." if latest else
                                "Tracker stage suggests a previous application." if row["history_unknown"] else
                                "Another tracked job with this company and title may already have an application."
                                if similar else None)
    if not row["prep_id"]:
        reason = "No preparation packet saved."
    elif not row["prep_approved"]:
        reason = "Latest preparation revision needs approval."
    elif not row["resume_id"]:
        reason = "The packet has no attached resume."
    elif row.get("current_resume_revision") is None:
        reason = "The attached resume draft has been removed."
    elif row["current_resume_revision"] != row["resume_revision"]:
        reason = "The resume changed after attachment. Prepare a packet with the current revision."
    elif not row["resume_approved"]:
        reason = "The attached resume revision needs current approval."
    else:
        reason = None
    row["readiness_reason"] = reason or "Packet and attached resume revisions approved."
    row["state"] = "draft" if reason else "ready"
    if latest:
        due = latest["follow_up_on"]
        row["state"] = "follow_up" if due and date.fromisoformat(due) <= date.today() else "submitted"
    return row


@router.get("")
def list_queue():
    with database() as db:
        return [queue_row(row) for row in db.execute(QUEUE + " ORDER BY title,company,j.id,d.id").fetchall()]


def locked_job(db, key):
    """Serialize discovery saves and queue writes using the listing and application rows."""
    kind, raw = key.split("-")
    number = int(raw)
    if number > 2147483647:
        raise HTTPException(404, "Job not found.")
    if kind == "listing":
        listing = db.execute("SELECT posting,tracked_job_id FROM discovered_jobs WHERE id=%s FOR UPDATE",
                             (number,)).fetchone()
        if not listing:
            raise HTTPException(404, "Listing not found.")
        number = listing["tracked_job_id"]
        if number is None:
            post = listing["posting"]
            number = track_job(db, post["title"], post["company"], (post.get("location") or "")[:120])["job_id"]
            db.execute("UPDATE discovered_jobs SET tracked_job_id=%s WHERE id=%s", (number, int(raw)))
    if not db.execute("SELECT job_id FROM applications WHERE job_id=%s FOR UPDATE", (number,)).fetchone():
        raise HTTPException(404, "Job not found.")
    return number


def get_row(db, job_id):
    return queue_row(db.execute(QUEUE + " WHERE j.id=%s", (job_id,)).fetchone())


def record(key, body, event):
    """Check history under lock, then append the event and update the stage atomically."""
    with database() as db:
        job_id = locked_job(db, key)
        row = get_row(db, job_id)
        latest = next(iter(row["history"]), None)
        if event == "submitted":
            if latest:
                raise HTTPException(409, "Submission already logged.")
            if row["duplicate_warning"] and not body.acknowledge_history:
                raise HTTPException(409, "Acknowledge the possible previous submission.")
        elif not latest or latest["id"] != body.expected_event_id:
            raise HTTPException(409, "Reload the latest submission before correcting it.")
        submitted_at = body.submitted_at or (latest["submitted_at"] if latest else datetime.now(timezone.utc))
        db.execute("""
            INSERT INTO application_queue_history
                (job_id,event,submitted_at,channel,reference,note,follow_up_on,url)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
        """, (job_id, event, submitted_at, body.channel, body.reference, body.note, body.follow_up_on,
              latest["url"] if latest else row["url"]))
        if event == "submitted":
            db.execute("UPDATE applications SET status='applied' WHERE job_id=%s AND status='saved'", (job_id,))
        return get_row(db, job_id)


@router.post("/{key}/submit")
def submit(key: Key, body: Submission):
    return record(key, body, "submitted")


@router.post("/{key}/correct")
def correct(key: Key, body: Correction):
    return record(key, body, "corrected")
