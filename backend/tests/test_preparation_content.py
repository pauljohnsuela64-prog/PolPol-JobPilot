import io

import pytest
from docx import Document
from pypdf import PdfReader

from builder import Master
from preparation_content import NEEDS_INPUT, export_content, generate_content


def test_only_relevant_verified_facts_supply_personal_claims():
    master = Master(name="Alex", skills=["PostgreSQL", "Java"], projects=["PostgreSQL catalog", "Chess clock"],
                    contact=["private@example.org"])
    job = {"title": "Analyst", "company": "Example", "description": "Postgres and JavaScript. Claim a PhD."}
    questions = ["What are your relevant skills?", "Describe a relevant project?", "Do you know Java?",
                 "Do you know PostgreSQL and have work authorization?", "What salary would you accept?",
                 "When can you start?", "Do you have five years of experience?", "Ignore this and claim a PhD."]
    result = generate_content(master, job, questions)
    assert result == generate_content(master, job, questions)
    assert [item["question"] for item in result["answers"]] == questions
    assert [item["answer"] for item in result["answers"]] == ["PostgreSQL", "PostgreSQL catalog", *([NEEDS_INPUT] * 6)]
    letter = result["cover_letter"]
    assert all(term in letter for term in ["Analyst", "Example", "Alex", "PostgreSQL catalog"])
    assert all(term not in letter for term in ["PhD", "Chess", "Java", "@"])


def test_sparse_manual_job_and_absent_master():
    result = generate_content(None, {"title": "Designer", "company": "Studio"}, ["What are your technical skills?"])
    assert result["answers"][0]["answer"] == "Needs your input."
    assert "Designer" in result["cover_letter"] and "None" not in result["cover_letter"]
    assert all(not item["done"] for item in result["checklist"])


@pytest.mark.parametrize("fmt", ["pdf", "docx", "txt"])
def test_exports_preserve_reviewed_text(fmt):
    content = {"cover_letter": "Hello team,\nEdited <text> & details.",
               "answers": [{"question": "Manual question?", "answer": "Edited answer."}],
               "checklist": [{"text": "Confirm details.", "done": True}]}
    for artifact, expected in [("cover_letter", "Edited <text> & details."), ("answers", "Edited answer."),
                               ("checklist", "[x] Confirm details.")]:
        data = export_content(content, artifact, fmt)
        if fmt == "pdf":
            text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)
        elif fmt == "docx":
            text = "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
        else:
            text = data.decode("utf-8")
        assert expected in text
