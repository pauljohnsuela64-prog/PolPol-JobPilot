import os

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row


def database():
    """Open a connection that commits on success and rolls back on error."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise HTTPException(503, "Set DATABASE_URL to connect to your local database.")
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=3)

def track_job(connection, title: str, company: str, location: str):
    # Both manual and discovery saves use this transaction to create a job and its stage.
    row = connection.execute(
        """
        INSERT INTO jobs (title, company, location) VALUES (%s, %s, %s)
        RETURNING id AS job_id, title, company, location, created_at
        """,
        (title, company, location),
    ).fetchone()
    application = connection.execute(
        "INSERT INTO applications (job_id) VALUES (%s) RETURNING status",
        (row["job_id"],),
    ).fetchone()
    return {**row, **application}
