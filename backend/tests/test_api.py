from fastapi.testclient import TestClient

from main import app, database


def test_health():
    response = TestClient(app, base_url="http://localhost").get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_invalid_job():
    response = TestClient(app, base_url="http://localhost").post("/api/jobs", json={"title": " ", "company": "Example"})
    assert response.status_code == 422


def test_application_workflow(test_database):
    from uuid import uuid4

    client = TestClient(app, base_url="http://localhost")
    title = f"Developer {uuid4()}"
    job = client.post("/api/jobs", json={"title": title, "company": "Example"})
    assert job.status_code == 201
    job_id = job.json()["job_id"]
    try:
        assert job.json()["status"] == "saved"
        updated = client.patch(f"/api/jobs/{job_id}", json={"status": "applied"})
        assert updated.status_code == 200
        assert updated.json()["status"] == "applied"
        assert updated.json() in client.get("/api/jobs").json()
    finally:
        with database() as connection:
            connection.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
