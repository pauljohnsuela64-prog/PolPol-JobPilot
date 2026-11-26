"""Reviewed, revision-bound handoffs for the local synthetic application form."""
import hashlib
import json
import re
import secrets
from collections import Counter
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field, StrictBool, StringConstraints

from builder import private_request
from database import database
from preparation_content import NEEDS_INPUT
from preparation_routes import Id, Input
from queue_routes import QUEUE, queue_row

EXTENSION_ORIGIN = "chrome-extension://ienannceaienjfffohgjidcfcmjfnlki"
TARGET_URL = "http://127.0.0.1:4179/application.html"
SAFE_QUESTIONS = {"What are your relevant skills?", "Describe a relevant project."}
FIELD_KEYS = {"full_name", "email", "phone", "experience", "education"}
Secret = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{43}$")]
Key = Annotated[str, StringConstraints(pattern=r"^(full_name|email|phone|experience|education|cover_letter|screening_[1-9][0-9]?)$")]
Revision = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


def extension_request(request: Request):
    if (request.headers.get("host") != "127.0.0.1:8000"
            or request.headers.get("origin") != EXTENSION_ORIGIN):
        raise HTTPException(403, "Use the JobPilot companion extension.")
    if request.method == "POST" and request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "Send a JSON request.")


router = APIRouter(prefix="/api/assistant")
workspace = APIRouter(dependencies=[Depends(private_request)])
extension = APIRouter(prefix="/extension", dependencies=[Depends(extension_request)])


class Review(Input):
    revision_key: Revision
    reviewed: StrictBool


class Copy(Input):
    revision_key: Revision
    field_key: Key


class Preview(Input):
    token: Secret
    target_url: Literal["http://127.0.0.1:4179/application.html"]


class Claim(Preview):
    field_keys: list[Key] = Field(min_length=1, max_length=7)


class Completion(Input):
    receipt: Secret
    filled_keys: list[Key] = Field(max_length=7)


def hash_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def field(key, label, value, source, status="verified"):
    if not value or value.strip().casefold().rstrip(".") == "needs your input":
        return dict(key=key, label=label, value=NEEDS_INPUT, source=source, status="unsupported")
    return dict(key=key, label=label, value=value, source=source, status=status)


def contact_value(lines, pattern, prefix):
    choices = set()
    for line in lines:
        value = re.sub(prefix, "", line, flags=re.I).strip()
        if pattern.fullmatch(value):
            choices.add(value)
    return next(iter(choices)) if len(choices) == 1 else None


def package_fields(facts, resume, prep):
    # A removed resume line must not reappear through the master resume.
    lines = {line.strip() for line in resume.splitlines()}
    contacts = [v for v in facts.get("contact", []) if v in lines]
    email = contact_value(contacts, re.compile(r"[^\s,@:;<>]+@[^\s,@:;<>]+\.[^\s,@:;<>]+"), r"^e-?mail:\s*")
    phone = contact_value(contacts, re.compile(r"(?=(?:\D*\d){10,15}\D*$)\+?[\d ()-]+"), r"^(?:phone|mobile|tel):\s*")
    source = "Verified master and approved resume"
    fields = [field("full_name", "Full name", facts.get("name") if facts.get("name") in lines else None, source),
              field("email", "Email", email, source), field("phone", "Phone", phone, source)]
    for key in ("experience", "education"):
        values = [v for v in facts.get(key, []) if v in lines or "- " + v in lines]
        fields.append(field(key, key.title(), "\n".join(values), source))
    fields.append(field("cover_letter", "Cover letter", prep["cover_letter"], "Approved preparation", "reviewed"))
    for i, answer in enumerate(prep["answers"], 1):
        fields.append(field(f"screening_{i}", answer["question"], answer["answer"], "Approved screening answer", "reviewed"))
    return fields


def synthetic_fields(fields):
    counts = Counter(f["label"] for f in fields if f["key"].startswith("screening_"))
    return [f for f in fields if f["key"] in FIELD_KEYS or
            (f["key"].startswith("screening_") and f["label"] in SAFE_QUESTIONS and counts[f["label"]] == 1)]


def ready(row):
    return row["state"] == "ready" and not row["duplicate_warning"]


def package(db, listing_id):
    # This locks against new preparation packets (their listing FK) and queue submissions.
    listing = db.execute("SELECT tracked_job_id FROM discovered_jobs WHERE id=%s FOR UPDATE", (listing_id,)).fetchone()
    if not listing:
        raise HTTPException(404, "Listing not found.")
    if listing["tracked_job_id"]:
        db.execute("SELECT job_id FROM applications WHERE job_id=%s FOR SHARE", (listing["tracked_job_id"],))
    row = queue_row(db.execute(QUEUE + " WHERE d.id=%s", (listing_id,)).fetchone())
    if not ready(row):
        raise HTTPException(409, row["duplicate_warning"] or row["readiness_reason"])
    prep = db.execute("SELECT content,revision,approved_revision FROM preparation_drafts WHERE id=%s FOR SHARE",
                      (row["prep_id"],)).fetchone()
    resume = db.execute("SELECT content,revision,approved_revision FROM resume_drafts WHERE id=%s FOR SHARE",
                        (row["resume_id"],)).fetchone()
    for doc, revision in [(prep, row["prep_revision"]), (resume, row["resume_revision"])]:
        if not doc or doc["revision"] != revision or doc["approved_revision"] != revision:
            raise HTTPException(409, "Approvals changed. Review the current revisions.")
    master = db.execute("SELECT facts FROM master_resume WHERE id=1 FOR SHARE").fetchone()
    facts = master["facts"] if master else {}
    fields = package_fields(facts, resume["content"], prep["content"])
    revision_key = hash_text(json.dumps([listing_id, row["prep_id"], row["prep_revision"], row["resume_id"],
                                        row["resume_revision"], facts, fields], sort_keys=True))
    return {"listing_id": listing_id, "title": row["title"], "company": row["company"], "revision_key": revision_key,
            "resume_revision": row["resume_revision"], "prep_revision": row["prep_revision"], "fields": fields}


def record(db, listing_id, event, count):
    db.execute("INSERT INTO assistant_activity(listing_id,event,field_count) VALUES (%s,%s,%s)",
               (listing_id, event, count))


def require_current(result, version):
    if result["revision_key"] != version:
        raise HTTPException(409, "The source information changed. Review a fresh package.")


@workspace.get("/jobs")
def jobs():
    with database() as db:
        rows = (queue_row(v) for v in db.execute(QUEUE + " ORDER BY title,company,d.id"))
        return [{k: row[k] for k in ("listing_id", "title", "company")} for row in rows if ready(row)]


def activity(db, listing_id):
    return db.execute("""SELECT event,field_count,created_at FROM assistant_activity
        WHERE listing_id=%s ORDER BY id DESC""", (listing_id,)).fetchall()


@workspace.get("/jobs/{listing_id}/activity")
def listing_activity(listing_id: Id):
    with database() as db:
        return activity(db, listing_id)


@workspace.get("/jobs/{listing_id}/package")
def preview_job(listing_id: Id):
    with database() as db:
        result = package(db, listing_id)
        result["activity"] = activity(db, listing_id)
        return result


@workspace.post("/jobs/{listing_id}/copy")
def copy_field(listing_id: Id, body: Copy):
    with database() as db:
        result = package(db, listing_id)
        require_current(result, body.revision_key)
        selected = next((f for f in result["fields"] if f["key"] == body.field_key and f["status"] != "unsupported"), None)
        if not selected:
            raise HTTPException(409, "This field needs your input.")
        record(db, listing_id, "copy_requested", 1)
        return {"value": selected["value"]}


@workspace.post("/jobs/{listing_id}/handoff")
def handoff(listing_id: Id, body: Review):
    if not body.reviewed:
        raise HTTPException(422, "Review the package before creating a handoff.")
    with database() as db:
        result = package(db, listing_id)
        require_current(result, body.revision_key)
        db.execute("""DELETE FROM assistant_handoffs WHERE listing_id=%s
                      AND (receipt_hash IS NULL OR expires_at <= now())""", (listing_id,))
        token = secrets.token_urlsafe(32)
        row = db.execute("""INSERT INTO assistant_handoffs(token_hash,listing_id,revision_key,expires_at)
                         VALUES (%s,%s,%s,now()+interval '5 minutes') RETURNING expires_at""",
                         (hash_text(token), listing_id, body.revision_key)).fetchone()
        record(db, listing_id, "prepared", sum(f["status"] != "unsupported" for f in result["fields"]))
        return {"token": token, **row}


def live_handoff(db, token):
    token_hash = hash_text(token)
    invalid = HTTPException(409, "Handoff expired or used. Review the package and create a new code.")
    row = db.execute("""SELECT listing_id FROM assistant_handoffs WHERE token_hash=%s AND receipt_hash IS NULL
                        AND expires_at > clock_timestamp()""", (token_hash,)).fetchone()
    if not row:
        raise invalid
    result = package(db, row["listing_id"])
    row = db.execute("""SELECT revision_key FROM assistant_handoffs WHERE token_hash=%s AND receipt_hash IS NULL
                        AND expires_at > clock_timestamp() FOR UPDATE""", (token_hash,)).fetchone()
    if not row:
        raise invalid
    require_current(result, row["revision_key"])
    return result


@extension.options("/{action}")
def preflight(action: Literal["preview", "claim", "complete"], request: Request, response: Response):
    headers = {h.strip().lower() for h in request.headers.get("access-control-request-headers", "").split(",") if h.strip()}
    if request.headers.get("access-control-request-method") != "POST" or headers - {"content-type"}:
        raise HTTPException(403, "Unsupported companion request.")
    response.headers["Access-Control-Allow-Methods"] = "POST"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return {}


@extension.post("/preview")
def preview_extension(body: Preview):
    with database() as db:
        result = live_handoff(db, body.token)
        return {"title": result["title"], "company": result["company"], "fields": synthetic_fields(result["fields"])}


@extension.post("/claim")
def claim(body: Claim):
    with database() as db:
        result = live_handoff(db, body.token)
        available = {f["key"]: f for f in synthetic_fields(result["fields"]) if f["status"] != "unsupported"}
        requested = set(body.field_keys)
        if len(requested) != len(body.field_keys) or not requested <= available.keys():
            raise HTTPException(409, "Unsupported or repeated field selection.")
        receipt = secrets.token_urlsafe(32)
        db.execute("""UPDATE assistant_handoffs SET receipt_hash=%s, authorized_keys=%s,
                      expires_at=clock_timestamp()+interval '5 minutes' WHERE token_hash=%s""",
                   (hash_text(receipt), body.field_keys, hash_text(body.token)))
        record(db, result["listing_id"], "fill_authorized", len(requested))
        return {"receipt": receipt, "fields": [available[k] for k in body.field_keys]}


@extension.post("/complete")
def complete(body: Completion):
    with database() as db:
        row = db.execute("""SELECT listing_id,authorized_keys,filled_keys FROM assistant_handoffs
                            WHERE receipt_hash=%s AND expires_at > clock_timestamp() FOR UPDATE""",
                         (hash_text(body.receipt),)).fetchone()
        keys = sorted(set(body.filled_keys))
        if not row or len(keys) != len(body.filled_keys) or not set(keys) <= set(row["authorized_keys"]):
            raise HTTPException(409, "Invalid fill receipt or field list.")
        if row["filled_keys"] is not None and row["filled_keys"] != keys:
            raise HTTPException(409, "A result was already recorded.")
        if row["filled_keys"] is None:
            db.execute("UPDATE assistant_handoffs SET filled_keys=%s WHERE receipt_hash=%s",
                       (keys, hash_text(body.receipt)))
            record(db, row["listing_id"], "filled", len(keys))
        return {"recorded": True}


router.include_router(workspace)
router.include_router(extension)
