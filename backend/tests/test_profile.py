import io
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from main import app


@pytest.fixture
def client():
    with TestClient(app, base_url="http://localhost") as test_client:
        yield test_client


def upload(client, content, name="resume.pdf"):
    return client.put(
        "/api/profile/resume",
        content=content,
        headers={"content-type": "application/pdf", "x-resume-filename": quote(name)},
    )


@pytest.mark.parametrize(
    ("content", "status"),
    [(b"<html>renamed to PDF</html>", 422), (b"%PDF-1.7\nfake", 422), (b"x" * (5 * 1024 * 1024 + 1), 413)],
    ids=["renamed-html", "fake-pdf-header", "oversize"],
)
def test_rejects_invalid_resume(client, content, status):
    assert upload(client, content).status_code == status


def test_rejects_invalid_preferences(client):
    assert client.put("/api/profile/preferences", json={"minimum_salary_php": -1}).status_code == 422


def test_preferences_saved_across_connections(client, test_database):
    preferences = {
        "job_titles": ["Junior Developer", "QA Engineer"],
        "locations": ["Manila", "Cebu"],
        "work_arrangement": "hybrid",
        "minimum_salary_php": 30000,
        "experience_level": "junior",
        "technical_skills": ["Python", "PostgreSQL"],
    }
    assert client.put("/api/profile/preferences", json=preferences).json() == preferences
    with TestClient(app, base_url="http://localhost") as fresh_client:
        assert fresh_client.get("/api/profile").json()["preferences"] == preferences


def test_replace_and_delete_resume(client, test_database, monkeypatch, tmp_path):
    monkeypatch.setenv("JOBPILOT_RESUME_DIR", str(tmp_path))
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf = io.BytesIO()
    writer.write(pdf)
    first = upload(client, pdf.getvalue(), "First Resume.pdf")
    assert first.status_code == 200
    assert first.json()["filename"] == "First Resume.pdf"
    assert first.json()["uploaded_at"]
    assert len(list(tmp_path.glob("*.pdf"))) == 1
    assert upload(client, b"fake").status_code == 422
    assert client.get("/api/profile").json()["resume"] == first.json()
    replacement = upload(client, pdf.getvalue(), "Updated Resume.pdf")
    assert replacement.status_code == 200
    assert client.get("/api/profile").json()["resume"] == replacement.json()
    assert replacement.json()["filename"] == "Updated Resume.pdf"
    stored = list(tmp_path.glob("*.pdf"))
    assert len(stored) == 1
    assert client.get(f"/resumes/{stored[0].name}").status_code == 404
    assert client.delete("/api/profile/resume").status_code == 204
    assert client.get("/api/profile").json()["resume"] is None
    assert not list(tmp_path.glob("*.pdf"))
